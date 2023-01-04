odoo.define('trilab_pl_partners_sync.autocomplete.Mixin', function (require) {
    'use strict';
    const PartnerAutocompleteMixin = require('partner.autocomplete.Mixin');
    PartnerAutocompleteMixin._isVAT = function (search_val) {
        let str = this._sanitizeVAT(search_val);
        if (str.match(/^\d{10}$/)) {
            str = 'PL' + str;
        }
        return checkVATNumber(str);
    };
    PartnerAutocompleteMixin._getCreateData = function (company) {
        const self = this;

        const removeUselessFields = function (company) {
            const fields = 'label,description,domain,logo,legal_name,ignored,email'.split(',');
            fields.forEach(function (field) {
                delete company[field];
            });

            const notEmptyFields = "country_id,state_id".split(',');
            notEmptyFields.forEach(function (field) {
                if (!company[field]) delete company[field];
            });
        };

        return new Promise(function (resolve) {
            // Fetch additional company info via Autocomplete Enrichment API
            const enrichPromise = self._enrichCompany(company);


            // Get logo
            const logoPromise = company.logo ? self._getCompanyLogo(company.logo) : false;
            self._whenAll([enrichPromise, logoPromise]).then(function (result) {
                let company_data = result[0];
                const logo_data = result[1];

                // The vat should be returned for free. This is the reason why
                // we add it into the data of 'company' even if an error such as
                // an insufficient credit error is raised.
                if (company_data.error && company_data.vat) {
                    company.vat = company_data.vat;
                }

                // Don't show error when x_pl_gus_update_date is true
                if (company_data.error && !company.x_pl_gus_update_date) {
                    if (company_data.error_message === 'Insufficient Credit') {
                        self._notifyNoCredits();
                    } else if (company_data.error_message === 'No Account Token') {
                        self._notifyAccountToken();
                    } else {
                        self.do_notify(false, company_data.error_message);
                    }
                    company_data = company;
                }else {
                    company_data = company;
                }

                if (_.isEmpty(company_data)) {
                    company_data = company;
                }

                // Delete attribute to avoid "Field_changed" errors
                removeUselessFields(company_data);

                // Assign VAT coming from parent VIES VAT query
                if (company.vat) {
                    company_data.vat = company.vat;
                }
                resolve({
                    company: company_data,
                    logo: logo_data
                });
            });
        });
    };
    return PartnerAutocompleteMixin;
});
