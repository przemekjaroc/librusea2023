
import ast
import base64
import json
import logging
import os
import shutil
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

import requests
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, padding
from cryptography.hazmat.primitives.asymmetric import padding as asymetric_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from jinja2 import Template

# noinspection PyProtectedMember
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class JPKTransfer(models.Model):
    _name = 'jpk.transfer'
    _description = _('JPK Transfer')
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(tracking=True, required=1)
    color = fields.Integer()
    active = fields.Boolean(default=True)

    settings_id = fields.Many2one('jpk.settings', required=1)
    jpk_type = fields.Selection([
        ('JPK', _('JPK - documents sent cyclically')),
        ('JPKAH', _('JPKAH - ad-hoc sending of documents during inspection'))],
        default='JPK', required=1)

    # Metadane transferu do podpisania
    unsigned_metadata_id = fields.Many2one('ir.attachment', string='Unsigned Metadata')
    unsigned_metadata_id_datas = fields.Binary(related='unsigned_metadata_id.datas', string='Unsigned Metadata Data')
    unsigned_metadata_id_name = fields.Char(related='unsigned_metadata_id.name', string='Unsigned Metadata Filename')

    # podpisane metadane transferu
    signed_metadata_id = fields.Many2one('ir.attachment', string='Signed Metadata')
    signed_metadata_id_datas = fields.Binary(related='signed_metadata_id.datas', readonly=0,
                                             string='Signed Metadata Data')
    signed_metadata_id_name = fields.Char(related='signed_metadata_id.name', readonly=0,
                                          string='Signed Metadata Filename')

    initial_response = fields.Char()

    # potwierdzenie transfeu
    confirmation_id = fields.Many2one('ir.attachment', string='Confirmation')
    confirmation_id_datas = fields.Binary(related='confirmation_id.datas', string='Confirmation Data')
    confirmation_id_name = fields.Char(related='confirmation_id.name', string='Confirmation Data Filename')

    secret_key = fields.Char()
    error_description = fields.Text(tracking=True)
    reference_number = fields.Char(tracking=True)
    reference_document = fields.Reference(selection=[('jpk.vat.7m', 'V7M')])
    state = fields.Selection([('draft', _('Draft')),
                              ('to_sign', _('To Sign')),
                              ('sent', _('Sent to IRS')),
                              ('confirmed', _('Confirmed')),
                              ('declined', _('Declined'))], default='draft', required=1, readonly=1,
                             group_expand='get_all_stages', tracking=True)
    document_ids = fields.One2many('jpk.document', 'transfer_id')
    last_description = fields.Char()

    def create_with_document(self, data):
        my_fields = self.fields_get_keys()
        rec = {'state': 'draft'}

        for k, v in data.items():
            if k in my_fields:
                rec[k] = v

        transfer = self.create([rec])
        data['transfer_id'] = transfer.id
        self.env['jpk.document'].create_with_attachment(data)
        return transfer

    def unlink(self):
        if any(transfer.state != 'declined' for transfer in self):
            raise ValidationError(_('You can only delete declined JPK Transfers.'))
        return super(JPKTransfer, self).unlink()

    @api.constrains('active')
    def constrains_active(self):
        transfers = self.filtered(lambda transfer: not transfer.active)
        if any(transfer.state not in ['confirmed', 'declined'] for transfer in transfers):
            raise ValidationError(_('You can only archive confirmed and declined JPK Transfers.'))

    # @api.constrains('jpk_type', 'document_ids')
    # def constrains_document_type(self):
    #     if any(document.jpk_type != self.jpk_type for document in self.document_ids):
    #         raise ValidationError(_('JPK type mismatch between transfer and documents.'))

    @api.onchange('signed_metadata_id_datas')
    def create_attachment(self):
        if not self.signed_metadata_id and self.signed_metadata_id_datas:
            self.signed_metadata_id = self.env['ir.attachment'].create(dict(
                name=self.signed_metadata_id_name,
                datas=self.signed_metadata_id_datas
            )).id

    # noinspection PyUnusedLocal
    @api.model
    def get_all_stages(self, stages, domain, order):
        # pelna lista stanow w widoku kanban
        return [key for key, val in type(self).state.selection]

    def validate_transfer(self):
        self.ensure_one()
        settings = self.settings_id

        if len(set(self.document_ids.mapped('name'))) != len(self.document_ids.mapped('name')):
            raise ValidationError(_('Selected files do not have unique file names!'))

        for document in self.document_ids:
            if len(document.name) < settings.JPK_MIN_FILENAME_LENGTH:
                raise ValidationError(
                    _('File %s has too short name, minimum is %d') % (document.name, settings.JPK_MIN_FILENAME_LENGTH))
            if len(document.name) > settings.JPK_MAX_FILENAME_LENGTH:
                raise ValidationError(
                    _('File %s has too long name, maximum is %d') % (document.name, settings.JPK_MAX_FILENAME_LENGTH))

    def create_transfer_request(self):
        self.ensure_one()

        if self.state != 'draft':
            return

        self.validate_transfer()

        settings = self.sudo().settings_id
        backend = default_backend()
        encryption_key = os.urandom(settings.JPK_ENCRYPTION_FILE_KEY_SIZE)
        self.secret_key = base64.b64encode(encryption_key)

        for document in self.document_ids:

            # hash sha256 base64 original_file_id

            origin_file_read = open(document.original_file_id.full_path, 'rb').read()

            digest = hashes.Hash(hashes.SHA256(), backend=backend)
            digest.update(origin_file_read)
            document.original_file_id.hash = base64.b64encode(digest.finalize())

            encryption_iv = os.urandom(settings.JPK_ENCRYPTION_FILE_IV_SIZE)
            document.iv = base64.b64encode(encryption_iv)
            tmp_dir = tempfile.mkdtemp()
            zip_file_name = "{0}.zip".format(document.name[:43])
            zip_file_path = os.path.join(tmp_dir, zip_file_name)
            with ZipFile(zip_file_path, 'w', ZIP_DEFLATED) as zipfile:
                zipfile.write(document.original_file_id.full_path, document.name)

            document.zip_file_id = self.env['ir.attachment'].create(dict(
                datas=base64.encodebytes(open(zip_file_path, 'rb').read()),
                name=zip_file_name,
                res_model='jpk.document',
                res_id=document.id
            ))

            file_part = 0
            data_copied = 0
            output_file = None
            input_file = open(document.zip_file_id.full_path, 'rb')
            output_file_name = None
            output_file_path = None
            cipher = Cipher(algorithms.AES(encryption_key), modes.CBC(encryption_iv), backend=backend)
            encryptor = None
            padder = None
            digest = None

            while True:
                data = input_file.read(64 * 1024)
                if data:
                    if output_file is None:
                        file_part += 1
                        encryptor = cipher.encryptor()
                        padder = padding.PKCS7(settings.JPK_ENCRYPTION_FILE_BLOCK_SIZE * 8).padder()
                        digest = hashes.Hash(hashes.MD5(), backend=backend)
                        output_file_name = "{0}.{1:03d}.aes".format(zip_file_name, file_part)
                        output_file_path = os.path.join(tmp_dir, output_file_name)
                        output_file = open(output_file_path, 'wb')

                    encrypted_data = encryptor.update(padder.update(data))
                    output_file.write(encrypted_data)
                    digest.update(encrypted_data)
                    data_copied += len(data)

                if data_copied >= settings.JPK_MAX_CHUNK_SIZE or (not data and output_file):
                    data = encryptor.update(padder.finalize()) + encryptor.finalize()
                    output_file.write(data)
                    output_file.close()
                    digest.update(data)
                    file_part_obj = self.env['ir.attachment'].create(dict(
                        datas=base64.encodebytes(open(output_file_path, 'rb').read()),
                        name=output_file_name,
                        res_model='jpk.file.part',
                        hash=base64.b64encode(digest.finalize())
                    ))

                    self.env['jpk.file.part'].create(dict(
                        transfer_document_id=document.id,
                        file_part_id=file_part_obj.id,
                        part_number=file_part
                    ))
                    output_file = None
                    data_copied = 0
                if not data:
                    break
            input_file.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)
            self.state = 'to_sign'

        key = open(settings.JPK_MF_PUBLIC_KEY_ID.full_path, 'rb').read()

        certificate = x509.load_pem_x509_certificate(key, backend=backend)
        public_key = certificate.public_key()
        encryption_key = base64.b64encode(public_key.encrypt(encryption_key, asymetric_padding.PKCS1v15()))

        dir_path = os.path.dirname(os.path.realpath(__file__))
        init_upload_template = open(dir_path + '/templates/InitUpload').read()
        t = Template(init_upload_template)
        unsigned_metadata = t.render(transfer=self, encryption_key=encryption_key.decode('utf-8'))

        self.unsigned_metadata_id = self.env['ir.attachment'].create(dict(
            datas=base64.b64encode(unsigned_metadata.encode('UTF-8')),
            name='unsigned_metadata.xml',
            res_model='jpk.transfer',
            res_id=self.id
        )).id

    def send_initial_request(self):

        if self.state != 'to_sign':
            return

        url = "{0}{1}".format(self.settings_id.endpoint_url, '/api/Storage/InitUploadSigned')
        headers = {'Content-Type': 'application/xml'}
        data = open(self.signed_metadata_id.full_path, 'rb').read()
        response = requests.post(url, data=data, headers=headers, verify=False)
        if response.ok:
            data = response.json()
            file_part_dict = {}
            self.initial_response = response.text
            self.reference_number = data.get('ReferenceNumber')
            for data_slice in data.get('RequestToUploadFileList'):
                file_part_dict[data_slice.get('FileName')] = data_slice
            for document in self.document_ids:
                for file_part in document.file_part_ids:
                    file_part.cloud_meta = file_part_dict[file_part.name]
                    file_part.blob_name = file_part_dict[file_part.name].get('BlobName')
                    self.upload_file_part(file_part)
            self.state = 'sent'
        else:
            raise ValidationError(response.text)

    def check_transfer_completness(self):
        transfer = self
        for document in transfer.document_ids:
            for part in document.file_part_ids:
                if not part.uploaded:
                    return
        self.send_final_request()

    def upload_file_part(self, file_part):
        if file_part.cloud_meta:
            headers = {}
            cloud_meta = ast.literal_eval(file_part.cloud_meta)

            for header in cloud_meta['HeaderList']:
                headers[header['Key']] = header['Value']
            response = requests.put(cloud_meta['Url'], data=open(file_part.file_part_id.full_path, 'rb').read(),
                                    headers=headers)
            if response.ok:
                file_part.uploaded = True
                self.check_transfer_completness()
            else:
                raise Exception('Error uploading file: %s' % response.text)
        else:
            raise Exception('Missing CloudMeta')

    def send_final_request(self):
        request = {
            'ReferenceNumber': self.reference_number,
            'AzureBlobNameList': []
        }
        for document in self.document_ids:
            for part in document.file_part_ids:
                request['AzureBlobNameList'].append(part.blob_name)

        url = "{0}{1}".format(self.settings_id.endpoint_url, '/api/Storage/FinishUpload')
        headers = {'Content-Type': 'application/json'}

        response = requests.post(url, data=json.dumps(request), headers=headers, verify=False)
        if response.ok:
            self.get_request_status()
        else:
            raise ValidationError(response.text)

    def get_request_status(self):

        if self.state != 'sent':
            return

        url = "{0}{1}{2}".format(self.settings_id.endpoint_url, '/api/Storage/Status/', self.reference_number)
        response = requests.get(url, verify=False)

        if response.ok:
            data = response.json()
            _logger.debug('get_request_status: %s', data)

            if self.last_description != data.get('Description'):
                self.message_post(body=_('Check request status: %s') % data.get('Description'))
                self.last_description = data.get('Description')

            code = data.get('Code')
            if code == 200:
                if data.get('Upo'):
                    confirmation_att = self.env['ir.attachment'].create(dict(
                        name='confirmation.xml',
                        datas=base64.b64encode(data.get('Upo').encode('UTF-8')),
                        res_model='jpk.transfer',
                        res_id=self.id,
                    ))
                    self.confirmation_id = confirmation_att.id
                    self.state = 'confirmed'
            elif code > 400:
                self.message_post(body=_('Check request status: [%s] %s') % (code, data.get('Description')))
                self.state = 'declined'
            else:
                self.state = 'sent'

        else:
            self.error_description = response.text
            self.state = 'declined'

    def move_to_declined(self):
        if self.state in ['draft', 'to_sign']:
            self.state = 'declined'

    @api.model
    def check_transfer_status_cron(self):
        for transfer in self.search([('state', '=', 'sent')]):
            transfer.get_request_status()
