"""Fresh committed planning view while the write transaction holds inventory locks.

MariaDB REPEATABLE-READ retains the request's earlier snapshot after waiting for
FOR UPDATE. A separate native Frappe connection starts a READ ONLY transaction
after lock acquisition. It cannot post, commit caller work, or release caller
locks. Only the validation calculation runs on it; insertion stays on the
original transaction. Uncommitted evidence is intentionally not releaseable.
"""
from contextlib import contextmanager
import frappe
from frappe.database import get_db


@contextmanager
def fresh_committed_read():
    original=frappe.local.db
    conf=frappe.local.conf
    reader=get_db(socket=conf.db_socket,host=conf.db_host,port=conf.db_port,
        user=conf.db_user,password=conf.db_password,cur_db_name=conf.db_name)
    try:
        reader.connect()
        reader.sql('SET TRANSACTION READ ONLY')
        reader.sql('START TRANSACTION WITH CONSISTENT SNAPSHOT')
        frappe.local.db=reader
        yield
    finally:
        frappe.local.db=original
        reader.rollback()
        reader.close()
