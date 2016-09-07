# -*- encoding: utf-8 -*-

import openerp
import openerp.tools.config as config
from openerp import SUPERUSER_ID


def get_registry(db_name):
    openerp.modules.registry.RegistryManager.check_registry_signaling(db_name)
    registry = openerp.registry(db_name)
    return registry


def need_new_bundle(threads_bundles_list, dbname):
    for bundle in threads_bundles_list:
        if bundle['dbname'] == dbname:
            return False
    return True


def get_parameter(dbname, key):
    db = openerp.sql_db.db_connect(dbname)
    registry = get_registry(dbname)
    with openerp.api.Environment.manage(), db.cursor() as cr:
        return registry['ir.config_parameter'].get_param(cr, SUPERUSER_ID, key)


def running_workers_num(workers):
    res = 0
    for r in workers:
        if r._running:
            res += 1
    return res


def _db_list():
    if config['db_name']:
        db_names = config['db_name'].split(',')
    else:
        db_names = openerp.service.db.list_dbs(True)
    return db_names


def get_num_of_odoo_threads(dbname):
    n = 1  # its minimum
    num = get_parameter(dbname, 'telegram.num_odoo_threads')
    if num:
        n += int(num)
    return n


def token_is_valid(token):
    if token and len(token) > 10:
        return True
    return False


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