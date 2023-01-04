# -*- coding: utf-8 -*-

from odoo import models, fields


class AccountAccountTag(models.Model):
    _inherit = 'account.account.tag'

    jpk_account_tag_ids = fields.One2many(comodel_name='jpk.account.tag', inverse_name='account_tag_id')
