import xmlrpc.client
import time
from odoo import models, fields, api
from odoo.exceptions import UserError

class TimeoutTransport(xmlrpc.client.Transport):
    def __init__(self, timeout=None):
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        if self.timeout:
            conn.timeout = self.timeout
        return conn

class SyncPicturesWizard(models.TransientModel):
    _name = 'sync.pictures.wizard'
    _description = 'Asistente de Sincronización de Imágenes'

    config_id = fields.Many2one('omni.sync.config', string='Conexión', required=True)
    brand_to_sync = fields.Char(string='Marca a Sincronizar')
    sync_all_brands = fields.Boolean(string='Sincronizar Todas las Marcas', default=True)
    execution_type = fields.Selection([
        ('manual', 'Manual'),
        ('auto', 'Automático')
    ], string='Tipo de Ejecución', default='manual')

    def action_sync_pictures(self):
        if not self.config_id:
            raise UserError('Debes seleccionar una configuración')
        
        if not self.config_id.sync_images:
            raise UserError('La sincronización de imágenes no está habilitada en esta configuración.')
        
        start_time = time.time()
        try:
            if self.sync_all_brands:
                marcas = [m.strip() for m in (self.config_id.brands_to_sync or '').split(',') if m.strip()]
            else:
                marcas = [self.brand_to_sync.strip()] if self.brand_to_sync else []
            
            if not marcas:
                marcas = ['TOTAL']

            timeout_transport = TimeoutTransport(timeout=self.config_id.timeout)
            
            # Conexión Remota (Origen de las imágenes)
            common_remote = xmlrpc.client.ServerProxy(f'{self.config_id.remote_url}/xmlrpc/2/common')
            uid_remote = common_remote.authenticate(self.config_id.remote_database, self.config_id.remote_username, self.config_id.remote_password, {})
            
            if not uid_remote:
                raise UserError('No se pudo autenticar con el servidor remoto')
            
            models_remote = xmlrpc.client.ServerProxy(f'{self.config_id.remote_url}/xmlrpc/2/object', transport=timeout_transport)
            
            for marca in marcas:
                self._procesar_marca(marca, models_remote, uid_remote)
            
            duration = time.time() - start_time
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Éxito',
                    'message': f'Sincronización completada en {duration:.2f} segundos',
                    'type': 'success',
                }
            }
        except Exception as e:
            raise UserError(f'Error durante la sincronización: {str(e)}')

    def _procesar_marca(self, marca, models_remote, uid_remote):
        log = self.env['sync.pictures.log'].create({
            'config_id': self.config_id.id,
            'brand': marca,
            'status': 'in_progress',
            'execution_type': self.execution_type,
        })
        
        line_vals = []
        try:
            # Buscar productos en remoto
            domain = [] if marca == 'TOTAL' else [('product_brand_id.name', '=', marca)]
            
            productos_remotos = models_remote.execute_kw(
                self.config_id.remote_database, uid_remote, self.config_id.remote_password,
                'product.product', 'search_read', [domain],
                {'fields': ['id', 'default_code', 'image_1920', 'name']}
            )
            
            log.write({'total_products': len(productos_remotos)})
            
            synced_count = 0
            skipped_count = 0
            
            for prod_remoto in productos_remotos:
                ref = prod_remoto.get('default_code')
                name = prod_remoto.get('name')
                
                line_val = {
                    'product_name': name,
                    'product_code': ref,
                }
                
                if not ref:
                    skipped_count += 1
                    line_val.update({'status': 'skipped', 'comment': 'Sin código de referencia'})
                    line_vals.append((0, 0, line_val))
                    continue
                
                if not prod_remoto.get('image_1920'):
                    skipped_count += 1
                    line_val.update({'status': 'skipped', 'comment': 'Sin imagen en origen'})
                    line_vals.append((0, 0, line_val))
                    continue
                
                prod_local = self.env['product.product'].search([('default_code', '=', ref)], limit=1)
                
                if not prod_local:
                    skipped_count += 1
                    line_val.update({'status': 'skipped', 'comment': 'No existe en base local'})
                    line_vals.append((0, 0, line_val))
                    continue
                    
                if prod_local.image_1920:
                    skipped_count += 1
                    line_val.update({'status': 'skipped', 'comment': 'Ya tiene imagen cargada'})
                    line_vals.append((0, 0, line_val))
                else:
                    prod_local.write({'image_1920': prod_remoto['image_1920']})
                    synced_count += 1
                    line_val.update({'status': 'synced', 'comment': 'Sincronizado correctamente'})
                    line_vals.append((0, 0, line_val))
            
            log.write({
                'status': 'completed',
                'products_synced': synced_count,
                'products_skipped': skipped_count,
                'line_ids': line_vals
            })
        except Exception as e:
            log.write({
                'status': 'failed', 
                'error_message': str(e),
            })
