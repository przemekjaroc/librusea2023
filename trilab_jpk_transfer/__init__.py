from . import models
from odoo import api, SUPERUSER_ID


def menu_switch(cr, state):
    env = api.Environment(cr, SUPERUSER_ID, {})
    menu_data = env['ir.model.data'].search([['name', '=', 'jpk_main_menu'],
                                             ['module', '=', 'trilab_jpk_base'],
                                             ['model', '=', 'ir.ui.menu']])

    if menu_data:
        menu_item = env['ir.ui.menu'].browse([menu_data.res_id])
        menu_item.write({'active': state})


def post_init_handler(cr, registry):
    menu_switch(cr, True)


def uninstall_handler(cr, registry):
    menu_switch(cr, False)
