# -*- encoding: utf-8 -*-

from openerp import models
from . import telegram
from . import telegram_bus
from . import controllers
from . import tools

import random
import datetime
import dateutil
import time
import sys
import openerp
from openerp.service.server import Worker
from openerp.service.server import PreforkServer
from openerp.tools.safe_eval import safe_eval
from openerp.tools.translate import _
import telebot
from telebot import TeleBot
import telebot.util as util
import openerp.tools.config as config
from openerp import SUPERUSER_ID
from openerp.exceptions import ValidationError
import threading
import logging
from telebot import apihelper, types, util

_logger = logging.getLogger('# ' + __name__)
_logger.setLevel(logging.DEBUG)


def telegram_worker():
    # monkey patch
    old_process_spawn = PreforkServer.process_spawn

    def process_spawn(self):
        old_process_spawn(self)
        while len(self.workers_telegram) < self.telegram_population:
            # only 1 telegram process we create.
            self.worker_spawn(WorkerTelegram, self.workers_telegram)

    PreforkServer.process_spawn = process_spawn
    old_init = PreforkServer.__init__

    def __init__(self, app):
        old_init(self, app)
        self.workers_telegram = {}
        self.telegram_population = 1
    PreforkServer.__init__ = __init__


class WorkerTelegram(Worker):
    """
        This is main singleton process for all other telegram purposes.
        It creates one TelegramDispatch (events bus), one OdooTelegramThread, several TeleBotMod and BotPollingThread threads.
    """

    def __init__(self, multi):
        super(WorkerTelegram, self).__init__(multi)
        self.interval = 10
        self.threads_bundles_list = []  # db_name, odoo_thread, odoo_dispatch
        self.singles_ran = False  # indicates one instance of odoo_dispatcher and odoo_thread exists
        self.odoo_thread = False
        self.odoo_dispatch = False

    def process_work(self):
        # this called by run() in while self.alive cycle
        db_names = tools._db_list()
        for dbname in db_names:
            registry = tools.get_registry(dbname)
            if registry.get('telegram.bus', False):
                # _logger.info("telegram.bus in %s" % db_name)
                if not tools.need_new_bundle(self.threads_bundles_list, dbname):
                    continue
                _logger.info("telegram.bus Need to create new bundle for %s" % dbname)
                self.odoo_dispatch = telegram_bus.TelegramDispatch().start()
                self.odoo_thread = OdooTelegramThread(self.interval, self.odoo_dispatch, dbname, False)
                self.odoo_thread.start()
                vals = {'dbname': dbname,
                        'odoo_thread': self.odoo_thread,
                        'odoo_dispatch': self.odoo_dispatch}
                self.threads_bundles_list.append(vals)
        time.sleep(self.interval / 2)

    def manage_threads(self):
        for bundle in self.threads_bundles_list:
            bot = bundle['bot']
            wp = bot.worker_pool
            new_num_threads = int(tools.get_parameter(bot.dbname, 'telegram.telegram_threads'))
            diff = new_num_threads - bot.telegram_threads
            if new_num_threads > bot.telegram_threads:
                # add new threads
                wp.workers += [util.WorkerThread(wp.on_exception, wp.tasks) for _ in range(diff)]
                bot.telegram_threads += diff
                _logger.info("Telegram workers increased and now its amount = %s" % tools.running_workers_num(wp.workers))
            elif new_num_threads < bot.telegram_threads:
                # decrease threads
                cnt = 0
                for i in range(len(wp.workers)):
                    if wp.workers[i]._running:
                        wp.workers[i].stop()
                        _logger.info('Telegram worker stop')
                        cnt += 1
                        if cnt >= -diff:
                            break
                cnt = 0
                for i in range(len(wp.workers)):
                    if not wp.workers[i]._running:
                        wp.workers[i].join()
                        _logger.info('Telegram worker join')
                        cnt += 1
                        if cnt >= -diff:
                            break
                bot.telegram_threads += diff
                _logger.info("Telegram workers decreased and now its amount = %s" % tools.running_workers_num(wp.workers))


class OdooTelegramThread(threading.Thread):
    """
        This is father-thread for odoo events execution-threads.
        When it started it at once spawns several execution threads.
        Then listens for some odoo events, pushed in telegram bus.
        If some event happened OdooTelegramThread find out about it by dispatch and gives to manage this event to one of executors.
        Executor do what needed in odoo_listener() method.
        Spawned threads are in odoo_thread_pool.
        Amount of threads = telegram.num_odoo_threads + 1
    """

    def __init__(self, interval, dispatch, dbname, bot):
        threading.Thread.__init__(self, name='OdooTelegramThread')
        self.daemon = True
        self.token = False
        self.interval = interval
        self.dispatch = dispatch
        self.bot = bot
        self.bot_thread = False
        self.last = 0
        self.dbname = dbname
        self.num_odoo_threads = tools.get_num_of_odoo_threads(dbname)
        self.odoo_thread_pool = util.ThreadPool(self.num_odoo_threads)

    def run(self):
        _logger.info("OdooTelegramThread started with %s threads" % self.num_odoo_threads)

        def listener(message, dbname, odoo_thread, bot):
            db = openerp.sql_db.db_connect(dbname)
            registry = tools.get_registry(dbname)
            with openerp.api.Environment.manage(), db.cursor() as cr:
                try:
                    registry['telegram.command'].odoo_listener(cr, SUPERUSER_ID, message, dbname, self, bot)
                except:
                    _logger.error('Error while proccessing Odoo message: %s' % message, exc_info=True)

        token = tools.get_parameter(self.dbname, 'telegram.token')
        if not self.bot and tools.token_is_valid(token):
            # need to launch bot manually on database start
            _logger.debug('on boot telegram start')
            db = openerp.sql_db.db_connect(self.dbname)
            registry = tools.get_registry(self.dbname)
            with openerp.api.Environment.manage(), db.cursor() as cr:
                registry['telegram.command'].telegram_proceed_ir_config(cr, SUPERUSER_ID, True, self.dbname)

        while True:
            # Exeptions ?
            if not token:
                continue
            # ask TelegramDispatch about some messages.
            msg_list = self.dispatch.poll(dbname=self.dbname, channels=['telegram_channel'], last=self.last)
            for msg in msg_list:
                if msg['id'] > self.last:
                    self.last = msg['id']
                    self.odoo_thread_pool.put(listener, msg, self.dbname, self, self.bot)
                    if self.odoo_thread_pool.exception_event.wait(0):
                        self.odoo_thread_pool.raise_exceptions()
