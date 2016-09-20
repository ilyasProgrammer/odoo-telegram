# -*- coding: utf-8 -*-
import logging
import time
from StringIO import StringIO
from telebot.apihelper import _convert_markup

from openerp import api
from openerp import models

_logger = logging.getLogger(__name__)


class TelegramCommand(models.Model):

    _inherit = "telegram.command"

    def _render(self, template, locals_dict, tsession):
        res = super(TelegramCommand, self)._render(template, locals_dict, tsession)
        res['markup'] = _convert_markup(locals_dict.get('data', {}).get('reply_markup', {}))
        _logger.debug("_render res['markup']: %s" % res['markup'])
        return res

    @api.multi
    def get_callback(self, locals_dict=None, tsession=None):
        self.ensure_one()
        _logger.debug("get_callback locals_dict: %s" % locals_dict)
        locals_dict = self._eval(self.callback_query_handler, locals_dict=locals_dict, tsession=tsession)
        return self._render(self.response_template, locals_dict, tsession)
