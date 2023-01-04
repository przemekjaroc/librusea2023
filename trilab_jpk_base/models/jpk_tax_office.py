import os

from odoo import fields, models
from lxml import etree


class JPKTaxOffice(models.Model):

    _name = 'jpk.taxoffice'
    _description = 'JPK Tax Offices'

    name = fields.Char(required=1, size=200, index=True)
    code = fields.Char(required=1, size=10, index=True)

    def load_from_xml(self):
        tree = etree.parse(os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                        '../data/KodyUrzedowSkarbowych_v4-0E.xsd'))
        ns = {'xsd': 'http://www.w3.org/2001/XMLSchema'}
        data = []
        for element in tree.xpath('//xsd:simpleType[@name="TKodUS"]/xsd:restriction/xsd:enumeration', namespaces=ns):
            data.append({
                'name': element.find('xsd:annotation/xsd:documentation', namespaces=ns).text,
                'code': element.attrib['value']
            })
        self.create(data)
