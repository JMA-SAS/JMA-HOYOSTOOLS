from odoo import models, fields

class ProductPricelist(models.Model):
    _inherit = 'product.pricelist'

    sync_to_remote = fields.Boolean(string='Sincronizar a Remoto', default=False)

class SyncProductMap(models.Model):
    _name = 'sync.product.map'
    _description = 'Mapeo de Productos Sincronizados'

    config_id = fields.Many2one('omni.sync.config', string='Configuración', ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Producto Local')
    remote_product_id = fields.Integer(string='ID Producto Remoto')
    last_sync_date = fields.Datetime(string='Última Sincronización')
    sync_status = fields.Selection([
        ('synced', 'Sincronizado'),
        ('failed', 'Fallido')
    ], string='Estado')
