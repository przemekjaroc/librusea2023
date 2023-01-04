from odoo import api, fields, models, _


class JPKTSettings(models.Model):
    _name = 'jpk.settings'
    _description = _('JPK Settings')

    name = fields.Char(required=1)
    active = fields.Boolean(default=True)

    endpoint_url = fields.Char(string=_('API Endpoint URL'), required=True)

    JPK_MIN_FILENAME_LENGTH = fields.Integer(default=5, required=True)
    JPK_MAX_FILENAME_LENGTH = fields.Integer(default=55, required=True)
    JPK_ENCRYPTION_FILE_KEY_SIZE = fields.Integer(default=32, required=True)
    JPK_ENCRYPTION_FILE_IV_SIZE = fields.Integer(default=16, required=True)
    JPK_ENCRYPTION_FILE_BLOCK_SIZE = fields.Integer(default=16, required=True)
    JPK_MAX_CHUNK_SIZE = fields.Integer(default=60 * 1024 * 1024, required=True)

    JPK_MF_PUBLIC_KEY_ID = fields.Many2one('ir.attachment')
    JPK_MF_PUBLIC_KEY_ID_datas = fields.Binary(related='JPK_MF_PUBLIC_KEY_ID.datas', readonly=False,
                                               string='JPK MF Public Key')
    JPK_MF_PUBLIC_KEY_ID_name = fields.Char(related='JPK_MF_PUBLIC_KEY_ID.name', readonly=False,
                                            string='JPK MF Public Key Filename')

    @api.onchange('JPK_MF_PUBLIC_KEY_ID_datas')
    def create_attachment(self):
        if not self.JPK_MF_PUBLIC_KEY_ID and self.JPK_MF_PUBLIC_KEY_ID_datas:
            self.JPK_MF_PUBLIC_KEY_ID = self.env['ir.attachment'].create(dict(
                name=self.JPK_MF_PUBLIC_KEY_ID_name,
                datas=self.JPK_MF_PUBLIC_KEY_ID_datas
            )).id
