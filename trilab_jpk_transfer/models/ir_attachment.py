import os

import odoo
from odoo import api, fields, models


class IrAttachment(models.Model):
    _inherit = 'ir.attachment'

    hash = fields.Char()
    full_path = fields.Char(compute='compute_full_path')
    size = fields.Integer(compute='compute_size')

    def compute_full_path(self):
        filestore = odoo.tools.config.filestore(self.env.cr.dbname)
        for record in self:
            record.full_path = os.path.join(filestore, record.store_fname)

    def compute_size(self):
        for record in self:
            record.size = os.path.getsize(record.full_path)
