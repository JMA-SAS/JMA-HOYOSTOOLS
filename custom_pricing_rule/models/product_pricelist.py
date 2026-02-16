from odoo import fields, models, api


class Pricelist(models.Model):
    _inherit = "product.pricelist"

    def _get_applicable_rules_domain(self, products, date, **kwargs):
        self and self.ensure_one()  # self is at most one record
        if products._name == 'product.template':
            templates_domain = ('product_tmpl_id', 'in', products.ids)
            products_domain = ('product_id.product_tmpl_id', 'in', products.ids)
        else:
            templates_domain = ('product_tmpl_id', 'in', products.product_tmpl_id.ids)
            products_domain = ('product_id', 'in', products.ids)

        return [
            ('pricelist_id', '=', self.id),
            '|', ('categ_id', '=', False), ('categ_id', 'parent_of', products.categ_id.ids),
            '|', ('apply_brand', '=', False), ('apply_brand', 'in', products.product_brand_id.ids),
            '|', ('apply_origin_country', '=', False), ('apply_origin_country', 'in', products.country_of_origin.ids),
            '|', ('apply_tag', '=', False), ('apply_tag', 'in', products.product_tag_ids.ids),
            '|', ('product_tmpl_id', '=', False), templates_domain,
            '|', ('product_id', '=', False), products_domain,
            '|', ('date_start', '=', False), ('date_start', '<=', date),
            '|', ('date_end', '=', False), ('date_end', '>=', date),
        ]
