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
import telebot
import sys
from lxml import etree
from openerp.addons.base.ir.ir_qweb import QWebContext

_logger = logging.getLogger('# Telegram')
_logger.setLevel(logging.DEBUG)
# telebot.logger.setLevel(logging.DEBUG)


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


globals_dict = {
    'datetime': datetime,
    'dateutil': dateutil,
    'time': time,
    'get_parameter': get_parameter,
    '_logger': _logger,
}


class TelegramCommand(models.Model):
    """
        Model represents Telegram commands that may be proceeded.
        Other modules can add new commands by adding some records of telegram.command model.
        Short commands gives result right after action_code is done.
        Long commands gives result after job is done, when appropriate notification appears in bus.

        Members:
          action_code - python code to execute task. Launched by telegram_listener
          action_response_template - Template of message, that user will receive immediately after he send command
          notify_code - python code to get data, computed after executed action code. Launched by odoo_listener (bus)
          notify_template - Template of message, that user will receive after job is done
          update_cache_code - python code to update cache. Launched by ir.actions.server
          group_ids - Who can use this command
          model_ids - These models changes initiates cache updates for this command.

    """
    _name = "telegram.command"

    name = fields.Char()
    cacheable = fields.Boolean()
    action_code = fields.Char()
    action_response_template = fields.Char()
    notify_code = fields.Char()
    notify_template = fields.Char()
    group_ids = fields.One2many('res.groups', 'telegram_command_id')
    model_ids = fields.Many2many('ir.model', 'command_to_model_rel', 'command_id', 'model_id')

    @api.model
    def telegram_listener(self, messages, bot):
        # python_code execution method
        for tele_message in messages:  # messages from telegram server
            res = self.env['telegram.command'].search([('name', '=', tele_message.text)], limit=1)
            if len(res) == 1:
                if not self.access_granted(res[0], tele_message.chat.id):
                    bot.send_message(tele_message.chat.id, 'Access denied. Command:  %s  .' % tele_message.text)
                    return
                locals_dict = {'env': self.env, 'bot': bot, 'tele_message': tele_message, 'TelegramUser': TelegramUser}
                need_computed_answer = True
                if res[0].id in bot.cache.vals:
                    command_cache = bot.cache.vals[res[0].id]
                    _logger.debug('got cache for this command')
                    _logger.debug(command_cache)
                    if command_cache['result']:
                        locals_dict.update(command_cache['result'])
                        self.render_and_send(bot, tele_message.chat.id, res[0].action_response_template, locals_dict, tele_message=tele_message)
                        need_computed_answer = False
                        _logger.debug('Sent answer from cache')
                    elif len(command_cache['users_results']):
                        for usr_cache_line in command_cache['users_results']:
                            locals_dict.update(usr_cache_line['result'])
                            tele_user = self.env['telegram.user'].search([('id', '=', usr_cache_line['user_id']),
                                                                          ('chat_id', '=', tele_message.chat.id)])
                            if len(tele_user) > 0:
                                self.render_and_send(bot, tele_message.chat.id, res[0].action_response_template, locals_dict, tele_message=tele_message)
                            need_computed_answer = False
                if need_computed_answer:
                    _logger.debug('No cache. Computing answer ...')
                    safe_eval(res[0].action_code, globals_dict, locals_dict, mode="exec", nocopy=True)
                    self.render_and_send(bot, tele_message.chat.id, res[0].action_response_template, locals_dict, tele_message=tele_message)
            elif len(res) > 1:
                raise ValidationError('Multiple values for %s' % res)
            else:
                bot.send_message(tele_message.chat.id, 'No such command:  %s  .' % tele_message.text)

    @api.model
    def odoo_listener(self, message, bot):
        bus_message = message['message']  # message from bus, not from telegram server.
        registry = openerp.registry(bot.db_name)
        db = openerp.sql_db.db_connect(bot.db_name)
        with openerp.api.Environment.manage(), db.cursor() as cr:
            _logger.debug('bus_message')
            _logger.debug(bus_message)
            if bus_message['action'] == 'update_cache':
                self.update_cache(bus_message, bot)
            else:
                command_id = registry['telegram.command'].search(cr, SUPERUSER_ID, [('name', '=', bus_message['action'])])
                command = registry['telegram.command'].browse(cr, SUPERUSER_ID, command_id)
                if len(command) == 1:
                    if command.notify_code:
                        locals_dict = {'bot': bot, 'bus_message': bus_message, 'TelegramUser': TelegramUser}
                        safe_eval(command.notify_code, globals_dict, locals_dict, mode="exec", nocopy=True)
                        _logger.debug('locals_dict')
                        _logger.debug(locals_dict)
                        self.render_and_send(bot, bus_message['chat_id'], command.notify_template, locals_dict, bus_message=bus_message)
                    else:
                        pass  # No notify_code for this command. Response code is optional.
                elif len(command) > 1:
                    raise ValidationError('Multiple values for %s' % command)

    def render_and_send(self, bot, chat_id, template, locals_dict, bus_message=False, tele_message=False):
        """Response or notify user. template - xml to render with locals_dict."""
        qweb = self.pool['ir.qweb']
        context = QWebContext(self._cr, self._uid, {})
        ctx = context.copy()
        ctx.update({'locals_dict': locals_dict['result']})
        dom = etree.fromstring(template)
        rend = qweb.render_node(dom, ctx)
        _logger.debug('render_and_send(): ' + rend)
        if bus_message:
            chat_id = bus_message['chat_id']
        elif tele_message:
            chat_id = tele_message.chat.id
        else:
            return
        bot.send_message(chat_id, rend, parse_mode='HTML')

    def update_cache_bus_message(self, cr, uid, ids, context):
        # Called by run_telegram_commands_cache_updates (ir.actions.server)
        _logger.debug('update_cache_bus_message(): start')
        found_commands_ids = self.pool['telegram.command'].search(cr, uid, [('model_ids.model', '=', context['active_model']), ('cacheable', '=', True)])
        if len(found_commands_ids):
            _logger.debug('update_cache_bus_message(): commands will got cache update:')
            _logger.debug(found_commands_ids)
            message = {'update_cache': True, 'model': context['active_model'], 'found_commands_ids': found_commands_ids}
            self.pool['telegram.bus'].sendone(cr, SUPERUSER_ID, 'telegram_channel', message)

    def update_cache(self, bus_message, bot):
        _logger.debug('update_cache() - command from bus')
        for command_id in bus_message['found_commands_ids']:
            command = self.env['telegram.command'].browse(command_id)
            locals_dict = {'bot': bot, 'env': self.env,'bus_message': bus_message, 'TelegramUser': TelegramUser}
            if len(command.group_ids):
                all_users_result = False
                users_results = {}
                users = self.env['res.user'].search([('groups_ids', 'in', command.group_ids)])
                for user in users:
                    locals_dict.update({'user_id': user.id})
                    safe_eval(command.action_code, globals_dict, locals_dict, mode="exec", nocopy=True)
                    users_results.update({'user_id': user.id, 'result': locals_dict['result']})
            else:
                users_results = False
                safe_eval(command.action_code, globals_dict, locals_dict, mode="exec", nocopy=True)
                all_users_result = locals_dict['result']
            bot.cache.update(command.id, all_users_result, users_results)

    def access_granted(self, command, chat_id):
        # granted or not ?
        if command.name == '/login':
            return True
        tele_user = self.env['telegram.user'].search([('chat_id', '=', chat_id)])
        user_groups = set(tele_user.res_user.groups_id)
        command_groups = set(self.env['res.groups'].search([('telegram_command_id', '=', command.id)]))
        if len(command_groups.intersection(user_groups)):
            return True
        return False


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


class ResGroups(models.Model):
    _inherit = 'res.groups'

    telegram_command_id = fields.Many2one('telegram.command')



    # query = """SELECT *
#            FROM mail_message as a, mail_message_res_partner_rel as b
#            WHERE a.id = b.mail_message_id
#            AND b.res_partner_id = %s""" % (5,)
# self.env.cr.execute(query)
# query_results = self.env.cr.dictfetchall()
#

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
