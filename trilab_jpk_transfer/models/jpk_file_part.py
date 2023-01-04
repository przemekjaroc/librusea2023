from odoo import fields, models, _


class JPKTFilePart(models.Model):

    _name = 'jpk.file.part'
    _description = _('JPK File Part')

    transfer_document_id = fields.Many2one('jpk.document', required=1, ondelete='cascade')
    name = fields.Char(related='file_part_id.name')
    active = fields.Boolean(related='transfer_document_id.transfer_id.active', store=True)
    file_part_id = fields.Many2one('ir.attachment')
    file_part_id_datas = fields.Binary(related='file_part_id.datas')
    file_part_id_name = fields.Char(related='file_part_id.name', string='Tmp Name')
    part_number = fields.Integer(default=1, required=1)
    cloud_meta = fields.Char()
    blob_name = fields.Char(size=250)
    uploaded = fields.Boolean()
