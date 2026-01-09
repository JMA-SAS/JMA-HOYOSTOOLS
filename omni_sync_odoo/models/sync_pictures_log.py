from odoo import models, fields

class SyncPicturesLog(models.Model):
    _name = 'sync.pictures.log'
    _description = 'Log de Sincronización de Imágenes'
    _order = 'create_date desc'

    config_id = fields.Many2one('omni.sync.config', string='Conexión', ondelete='cascade')
    brand = fields.Char(string='Marca')
    total_products = fields.Integer(string='Total Productos')
    products_synced = fields.Integer(string='Sincronizados')
    products_skipped = fields.Integer(string='Omitidos')
    products_failed = fields.Integer(string='Fallidos')
    pricelists_synced = fields.Integer(string='Listas Sincronizadas')
    pricelists_failed = fields.Integer(string='Listas Fallidas')
    execution_type = fields.Selection([
        ('manual', 'Manual'),
        ('auto', 'Automático')
    ], string='Tipo de Ejecución', default='manual')
    status = fields.Selection([
        ('in_progress', 'En Progreso'),
        ('completed', 'Completado'),
        ('failed', 'Fallido')
    ], string='Estado')
    error_message = fields.Text(string='Mensaje de Error')
    duration = fields.Float(string='Duración (seg)')
    
    line_ids = fields.One2many('sync.pictures.log.line', 'log_id', string='Detalles de Productos')
    pricelist_line_ids = fields.One2many('sync.pricelist.log.line', 'log_id', string='Detalles de Listas de Precios')

class SyncPricelistLogLine(models.Model):
    _name = 'sync.pricelist.log.line'
    _description = 'Línea de Log de Sincronización de Listas de Precios'

    log_id = fields.Many2one('sync.pictures.log', string='Log', ondelete='cascade')
    pricelist_name = fields.Char(string='Lista de Precios')
    status = fields.Selection([
        ('synced', 'Sincronizado'),
        ('failed', 'Fallido')
    ], string='Estado')
    comment = fields.Char(string='Comentario')

class SyncPicturesLogLine(models.Model):
    _name = 'sync.pictures.log.line'
    _description = 'Línea de Log de Sincronización de Imágenes'

    log_id = fields.Many2one('sync.pictures.log', string='Log', ondelete='cascade')
    product_name = fields.Char(string='Producto')
    product_code = fields.Char(string='Código')
    status = fields.Selection([
        ('synced', 'Sincronizado'),
        ('skipped', 'Omitido'),
        ('failed', 'Fallido')
    ], string='Estado')
    comment = fields.Char(string='Comentario')
