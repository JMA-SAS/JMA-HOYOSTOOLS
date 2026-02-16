from odoo import fields, models, api, _
from odoo.exceptions import ValidationError
from odoo.tools import format_datetime, formatLang


class ProductPricelistItem(models.Model):
    _inherit = 'product.pricelist.item'

    applied_on = fields.Selection(
        selection=[
            ('3_global', "All Products"),
            ('2_product_category', "Product Category"),
            ('1_product', "Product"),
            ('0_product_variant', "Product Variant"),
            ('4_product_brand', "Product Brand"),
            ('5_product_origin_country', "Product Origin Country"),
            ('6_product_tag', "Product Tag"),
        ],
        string="Apply On",
        default='3_global',
        required=True,
        help="Pricelist Item applicable on selected option")

    apply_brand = fields.Many2one('product.brand', string="Brand")
    apply_origin_country = fields.Many2one('res.country', string="Country of origin")
    apply_tag = fields.Many2one('product.tag', string="Tag")

    @api.depends('applied_on', 'categ_id', 'product_tmpl_id', 'product_id', 'compute_price', 'fixed_price', \
                 'pricelist_id', 'percent_price', 'price_discount', 'price_surcharge', 'apply_brand', 'apply_origin_country', 'apply_tag')
    def _compute_name_and_price(self):
        res = super(ProductPricelistItem, self)._compute_name_and_price()
        for item in self:
            if item.applied_on == '4_product_brand':
                item.name = _("Brand: %s") % item.apply_brand.display_name
            elif item.applied_on == '5_product_origin_country':
                item.name = _("Origin Country: %s") % item.apply_origin_country.display_name
            elif item.applied_on == '6_product_tag':
                item.name = _("Tag: %s") % item.apply_tag.display_name
        return res

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if values.get('applied_on', False):
                # Ensure item consistency for later searches.
                applied_on = values['applied_on']
                if applied_on == '3_global':
                    values.update(dict(product_id=None, product_tmpl_id=None, categ_id=None, apply_brand=None, apply_origin_country=None, apply_tag=None))
                elif applied_on == '2_product_category':
                    values.update(dict(product_id=None, product_tmpl_id=None, apply_brand=None, apply_origin_country=None, apply_tag=None))
                elif applied_on == '1_product':
                    values.update(dict(product_id=None, categ_id=None, apply_brand=None, apply_origin_country=None, apply_tag=None))
                elif applied_on == '0_product_variant':
                    values.update(dict(categ_id=None, apply_brand=None, apply_origin_country=None, apply_tag=None))
                elif applied_on == '4_product_brand':
                    values.update(dict(product_id=None, product_tmpl_id=None, categ_id=None, apply_origin_country=None, apply_tag=None))
                elif applied_on == '5_product_origin_country':
                    values.update(dict(product_id=None, product_tmpl_id=None, categ_id=None, apply_brand=None, apply_tag=None))
                elif applied_on == '6_product_tag':
                    values.update(dict(product_id=None, product_tmpl_id=None, categ_id=None, apply_brand=None, apply_origin_country=None))
        return super().create(vals_list)

    def write(self, values):
        if values.get('applied_on', False):
            # Ensure item consistency for later searches.
            applied_on = values['applied_on']
            if applied_on == '3_global':
                values.update(dict(product_id=None, product_tmpl_id=None, categ_id=None, apply_brand=None,
                                   apply_origin_country=None, apply_tag=None))
            elif applied_on == '2_product_category':
                values.update(dict(product_id=None, product_tmpl_id=None, apply_brand=None, apply_origin_country=None,
                                   apply_tag=None))
            elif applied_on == '1_product':
                values.update(
                    dict(product_id=None, categ_id=None, apply_brand=None, apply_origin_country=None, apply_tag=None))
            elif applied_on == '0_product_variant':
                values.update(dict(categ_id=None, apply_brand=None, apply_origin_country=None, apply_tag=None))
            elif applied_on == '4_product_brand':
                values.update(dict(product_id=None, product_tmpl_id=None, categ_id=None, apply_origin_country=None,
                                   apply_tag=None))
            elif applied_on == '5_product_origin_country':
                values.update(
                    dict(product_id=None, product_tmpl_id=None, categ_id=None, apply_brand=None, apply_tag=None))
            elif applied_on == '6_product_tag':
                values.update(dict(product_id=None, product_tmpl_id=None, categ_id=None, apply_brand=None,
                                   apply_origin_country=None))
        return super().write(values)

    def _is_applicable_for(self, product, qty_in_product_uom):
        """Check whether the current rule is valid for the given product & qty.

        Note: self.ensure_one()

        :param product: product record (product.product/product.template)
        :param float qty_in_product_uom: quantity, expressed in product UoM
        :returns: Whether rules is valid or not
        :rtype: bool
        """
        self.ensure_one()
        product.ensure_one()
        res = True

        is_product_template = product._name == 'product.template'
        if self.min_quantity and qty_in_product_uom < self.min_quantity:
            res = False

        elif self.applied_on == "2_product_category":
            if (
                product.categ_id != self.categ_id
                and not product.categ_id.parent_path.startswith(self.categ_id.parent_path)
            ):
                res = False
        elif self.applied_on == "4_product_brand":
            if product.product_brand_id != self.apply_brand:
                res = False
        elif self.applied_on == "5_product_origin_country":
            if product.country_of_origin != self.apply_origin_country:
                res = False
        elif self.applied_on == "6_product_tag":
            if self.apply_tag not in product.product_tag_ids:
                res = False
        else:
            # Applied on a specific product template/variant
            if is_product_template:
                if self.applied_on == "1_product" and product.id != self.product_tmpl_id.id:
                    res = False
                elif self.applied_on == "0_product_variant" and not (
                    product.product_variant_count == 1
                    and product.product_variant_id.id == self.product_id.id
                ):
                    # product self acceptable on template if has only one variant
                    res = False
            else:
                if self.applied_on == "1_product" and product.product_tmpl_id.id != self.product_tmpl_id.id:
                    res = False
                elif self.applied_on == "0_product_variant" and product.id != self.product_id.id:
                    res = False

        return res










