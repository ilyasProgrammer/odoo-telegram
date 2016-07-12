# -*- coding:utf-8 -*-

import openerp
from openerp import api, models, fields
import openerp.addons.auth_signup.res_users as res_users
from openerp.http import request
from openerp import SUPERUSER_ID
from openerp.addons.base.ir import ir_qweb
from openerp.exceptions import ValidationError
from openerp.tools.safe_eval import safe_eval
import datetime
import dateutil
import time
import logging
from lxml import etree
from openerp.addons.base.ir.ir_qweb import QWebContext

_logger = logging.getLogger('# Telegram')

SAFE_EVAL_BASE = {
    'datetime': datetime,
    'dateutil': dateutil,
    'time': time,
}


class TelegramCommand(models.Model):
    """
        Model represents Telegram commands that may be proceeded.
        Other modules can add new commands by adding some records of telegram.command model.
        Command must have:
          - python_code to execute;
          - response_code to handle odoo response on executed python_code as optional;
          - and web controllers if it is needed.
    """
    _name = "telegram.command"

    name = fields.Char()
    python_code = fields.Char()
    response_code = fields.Char()
    groups = fields.Char()
    response_template = fields.Char()
    notify_template = fields.Char()

    @api.model
    def telegram_listener(self, messages, bot):
        # python_code execution method
        for m in messages:
            res = self.env['telegram.command'].search([('name', '=', m.text)], limit=1)
            if len(res) == 1:
                locals_dict = {'self': self, 'bot': bot, 'm': m,
                               'TelegramUser': TelegramUser,
                               'get_parameter': get_parameter}
                safe_eval(res[0].python_code, SAFE_EVAL_BASE, locals_dict, mode="exec", nocopy=True)
                self.notify(bot, m, res[0].response_template, locals_dict)
            elif len(res) > 1:
                raise ValidationError('Multiple values for %s' % res)
            else:
                bot.send_message(m.chat.id, 'No such command: < %s > .' % m.text)

    def odoo_listener(self, message, bot):
        m = message['message']
        print '# 111:'
        res = self.pool['telegram.command'].search(self._cr, self._uid, [('name', '=', m.text)], limit=1)
        print '# 222:'
        if len(res) == 1:
            if res[0].response_code:
                locals_dict = {'self': self, 'bot': bot, 'm': m,
                               'TelegramUser': TelegramUser,
                               'get_parameter': get_parameter}
                safe_eval(res[0].response_code, SAFE_EVAL_BASE, locals_dict, mode="exec", nocopy=True)
                self.notify(bot, m, res[0].notify_template, locals_dict)
            else:
                pass  # No response code for this command. Response code is optional.
        elif len(res) > 1:
            raise ValidationError('Multiple values for %s' % res)

    def notify(self, bot, m, template, locals_dict):
        """Response or notify user"""
        qweb = self.pool['ir.qweb']
        context = QWebContext(self._cr, self._uid, {})
        ctx = context.copy()
        ctx.update({'locals_dict': locals_dict})
        dom = etree.fromstring(template)
        rend = qweb.render_node(dom, ctx)
        bot.send_message(m.chat.id, rend)


class TelegramUser(models.TransientModel):
    _name = "telegram.user"

    chat_id = fields.Char()  # Primary key
    token = fields.Char()
    logged_in = fields.Boolean()
    res_user = fields.Many2one('res.users')  # Primary key

    @staticmethod
    def register_user(tele_env, chat_id):
        tele_user_id = tele_env['telegram.user'].search([('chat_id', '=', chat_id)])
        if len(tele_user_id) == 0:
            login_token = res_users.random_token()
            vals = {'chat_id': chat_id, 'token': login_token}
            new_tele_user = tele_env['telegram.user'].create(vals)
        else:
            tele_user_obj = tele_env['telegram.user'].browse(tele_user_id.id)
            login_token = tele_user_obj.token  # user already exists

        return login_token

    @staticmethod
    def check_access(tele_env, chat_id, command):
        pass
        # tele_user_id = tele_env['telegram.user'].search([('chat_id', '=', chat_id)])
        # tele_user_obj = tele_env['telegram.user'].browse(tele_user_id)
        # TODO


# query = """SELECT *
#            FROM mail_message as a, mail_message_res_partner_rel as b
#            WHERE a.id = b.mail_message_id
#            AND b.res_partner_id = %s""" % (5,)
# self.env.cr.execute(query)
# query_results = self.env.cr.dictfetchall()
#


def get_parameter(db_name, key):
    db = openerp.sql_db.db_connect(db_name)
    registry = openerp.registry(db_name)
    result = None
    with openerp.api.Environment.manage(), db.cursor() as cr:
        res = registry['ir.config_parameter'].search(cr, SUPERUSER_ID, [('key', '=', key)])
        if len(res) == 1:
            val = registry['ir.config_parameter'].browse(cr, SUPERUSER_ID, res[0])
            result = val.value
        elif len(res) < 1:
            _logger.debug('# WARNING. No value for key %s' % key)
            return None
    return result


def dump(obj):
    for attr in dir(obj):
        print "obj.%s = %s" % (attr, getattr(obj, attr))


def dumpclean(obj):
    if type(obj) == dict:
        for k, v in obj.items():
            if hasattr(v, '__iter__'):
                print k
                dumpclean(v)
            else:
                print '%s : %s' % (k, v)
    elif type(obj) == list:
        for v in obj:
            if hasattr(v, '__iter__'):
                dumpclean(v)
            else:
                print v
    else:
        print obj
