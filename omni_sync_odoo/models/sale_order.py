from odoo import models, fields, api, _
from odoo.exceptions import UserError
import xmlrpc.client

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    is_synced = fields.Boolean(
        string="Sincronizado", 
        readonly=True, 
        copy=False,
        help="Indica si esta línea de pedido ha sido procesada y enviada exitosamente a la instancia remota."
    )
    sync_status = fields.Selection([
        ('synced', 'Sincronizado'),
        ('failed', 'No Sincronizado')
    ], string="Estado Sinc.", readonly=True, copy=False, help="Estado detallado de la sincronización para esta línea específica.")

class SaleOrder(models.Model):
    _inherit = 'sale.order'

    is_synced = fields.Boolean(
        string="Sincronizado", 
        readonly=True, 
        copy=False,
        help="Indica si el pedido completo ha sido sincronizado con el servidor remoto."
    )
    meli_tracking_pdf = fields.Binary(
        string="Guía Meli (PDF)", 
        attachment=True,
        help="Archivo PDF de la guía de despacho de Mercado Libre para ser enviado al remoto."
    )
    meli_tracking_filename = fields.Char(
        string="Nombre del archivo",
        help="Nombre técnico del archivo PDF de la guía."
    )
    sync_log = fields.Html(
        string="Resumen de Sincronización", 
        readonly=True, 
        copy=False,
        help="Registro visual detallado del resultado de la sincronización, incluyendo IDs remotos y marcas de tiempo."
    )
    remote_order_ref = fields.Char(
        string="Referencia Remota", 
        readonly=True, 
        copy=False,
        help="Nombre o número de referencia asignado al pedido en la instancia de Odoo remota."
    )
    is_remote_order = fields.Boolean(
        string="Es Pedido Remoto", 
        default=False, 
        help="Marca técnica para identificar pedidos que vienen desde otra instancia y evitar bucles de re-sincronización infinita."
    )

    # Campos para el dashboard
    @api.model
    def get_sync_stats(self):
        """Devuelve estadísticas de sincronización para el dashboard de ventas"""
        synced_orders = self.search([('is_synced', '=', True)])
        total_count = len(synced_orders)
        avg_value = sum(synced_orders.mapped('amount_total')) / total_count if total_count > 0 else 0.0
        return {
            'total_count': total_count,
            'avg_value': avg_value,
        }

    def action_confirm(self):
        """Extensión de la confirmación para disparar la sincronización automática."""
        res = super(SaleOrder, self).action_confirm()
        for order in self:
            # Sincronizar automáticamente al confirmar si no está sincronizado y NO es un pedido remoto
            if not order.is_synced and not order.is_remote_order:
                try:
                    order.action_sync_order()
                except Exception as e:
                    # No bloqueamos la confirmación si falla la sincronización, pero lo registramos
                    order.message_post(body=f"Error en sincronización automática: {str(e)}")
        return res

    def action_sync_order(self):
        """Proceso principal de envío de pedido a instancia remota."""
        for order in self:
            if order.is_synced:
                raise UserError(_('Este pedido ya fue sincronizado.'))
            if order.is_remote_order:
                raise UserError(_('Este pedido proviene de una instancia remota y no puede ser re-sincronizado.'))

            # Buscar configuración específica desde el contexto (para multicliente) o la primera activa
            config_id = self.env.context.get('omni_sync_config_id')
            if config_id:
                config_rec = self.env['omni.sync.config'].browse(config_id)
            else:
                config_rec = self.env['omni.sync.config'].search([('active', '=', True), ('sync_sales', '=', True)], limit=1)
            
            if not config_rec or not config_rec.active or not config_rec.sync_sales:
                raise UserError(_('No hay una configuración válida para sincronización de ventas.'))

            url = config_rec.remote_url
            db = config_rec.remote_database
            user = config_rec.remote_username
            password = config_rec.remote_password

            try:
                common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
                uid = common.authenticate(db, user, password, {})
                if not uid:
                    raise UserError(_('Autenticación fallida en el servidor remoto.'))

                models_rpc = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')

                # Validar si el modelo sale.order existe en el remoto
                try:
                    models_rpc.execute_kw(db, uid, password, 'sale.order', 'search', [[]], {'limit': 1})
                except Exception:
                    raise UserError(_('El módulo de Ventas (sale.order) no parece estar instalado en el servidor remoto.'))

                # Partner de la compañía (Contacto de la compañía de origen)
                company_partner = order.company_id.partner_id
                if not company_partner:
                    raise UserError(_('La compañía del pedido no tiene un partner asignado. Por favor, asigne un contacto a la compañía.'))

                # Buscamos en el remoto un partner que coincida con los datos de nuestra compañía
                partner_domain = [('name', '=', company_partner.name)]
                if company_partner.vat:
                    partner_domain = ['|', ('vat', '=', company_partner.vat)] + partner_domain
                
                partner_ids = models_rpc.execute_kw(db, uid, password, 'res.partner', 'search', [partner_domain], {'limit': 1})

                if partner_ids:
                    remote_partner_id = partner_ids[0]
                else:
                    # Si no existe, lo creamos con los datos de nuestra compañía
                    remote_partner_id = models_rpc.execute_kw(db, uid, password, 'res.partner', 'create', [{
                        'name': company_partner.name,
                        'vat': company_partner.vat,
                        'street': company_partner.street,
                        'city': company_partner.city,
                        'phone': company_partner.phone,
                        'email': company_partner.email,
                        'is_company': True,
                    }])

                # Líneas del pedido
                remote_lines = []
                for line in order.order_line:
                    if not line.product_id.default_code:
                        continue

                    prod_ids = models_rpc.execute_kw(
                        db, uid, password, 'product.product', 'search',
                        [[('default_code', '=', line.product_id.default_code)]],
                        {'limit': 1}
                    )

                    if prod_ids:
                        remote_lines.append((0, 0, {
                            'product_id': prod_ids[0],
                            'product_uom_qty': line.product_uom_qty,
                            'name': line.name,
                        }))
                        line.write({'is_synced': True, 'sync_status': 'synced'})
                    else:
                        line.write({'is_synced': False, 'sync_status': 'failed'})

                if not remote_lines:
                    # Si no hay líneas válidas, simplemente registramos una alerta y notificamos sin bloquear
                    order.write({
                        'sync_log': "<div class='alert alert-warning' style='padding: 15px; border-radius: 8px; border-left: 5px solid #ffc107; background: #fff3cd; color: #856404;'><strong>Sincronización Omitida:</strong> Ninguno de los productos de este pedido existe en la base remota. Se asume que son productos de otros proveedores.</div>"
                    })
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Sincronización omitida'),
                            'message': _('No se encontraron productos coincidentes en el remoto. El pedido no fue sincronizado.'),
                            'type': 'warning',
                            'sticky': False,
                        }
                    }

                # Campaña
                remote_campaign_id = False
                if order.campaign_id:
                    campaign_ids = models_rpc.execute_kw(
                        db, uid, password, 'utm.campaign', 'search',
                        [[('name', '=', order.campaign_id.name)]], {'limit': 1}
                    )
                    if campaign_ids:
                        remote_campaign_id = campaign_ids[0]
                    else:
                        remote_campaign_id = models_rpc.execute_kw(
                            db, uid, password, 'utm.campaign', 'create', [{'name': order.campaign_id.name}]
                        )

                # Validar si el campo is_remote_order existe en el remoto antes de enviarlo
                remote_fields = models_rpc.execute_kw(db, uid, password, 'sale.order', 'fields_get', [[]], {'attributes': ['string']})
                
                # Crear pedido remoto
                order_data = {
                    'partner_id': remote_partner_id,
                    'origin': order.origin or order.name,
                    'date_order': str(order.date_order),
                    'order_line': remote_lines,
                }
                
                if 'is_remote_order' in remote_fields:
                    order_data['is_remote_order'] = True  # Marcamos en el destino que es un pedido remoto
                if order.meli_tracking_pdf and order.meli_tracking_filename:
                    order_data['meli_tracking_pdf'] = order.meli_tracking_pdf
                    order_data['meli_tracking_filename'] = order.meli_tracking_filename
                if remote_campaign_id:
                    order_data['campaign_id'] = remote_campaign_id

                remote_order_id = models_rpc.execute_kw(db, uid, password, 'sale.order', 'create', [order_data])
                
                # Obtener el nombre del pedido remoto si es posible
                remote_order_name = models_rpc.execute_kw(db, uid, password, 'sale.order', 'read', [[remote_order_id]], {'fields': ['name']})
                remote_ref = remote_order_name[0].get('name') if remote_order_name else str(remote_order_id)

                # Generar Log de Resumen
                log_html = f"""
                    <div style="width: 100%; margin-top: 10px; font-family: sans-serif;">
                        <table style="width: 100%; border-collapse: separate; border-spacing: 10px; table-layout: fixed;">
                            <tr>
                                <td style="width: 25%; background: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 5px solid #007bff; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                                    <div style="font-size: 11px; color: #666; text-uppercase; font-weight: bold; margin-bottom: 5px;">Referencia Remota</div>
                                    <div style="font-size: 16px; color: #007bff; font-weight: bold;">{remote_ref}</div>
                                </td>
                                <td style="width: 25%; background: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 5px solid #28a745; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                                    <div style="font-size: 11px; color: #666; text-uppercase; font-weight: bold; margin-bottom: 5px;">ID Remoto</div>
                                    <div style="font-size: 16px; color: #28a745; font-weight: bold;">{remote_order_id}</div>
                                </td>
                                <td style="width: 25%; background: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 5px solid #ffc107; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                                    <div style="font-size: 11px; color: #666; text-uppercase; font-weight: bold; margin-bottom: 5px;">Base de Datos</div>
                                    <div style="font-size: 14px; color: #333; font-weight: bold; word-break: break-all;">{db}</div>
                                </td>
                                <td style="width: 25%; background: #f8f9fa; padding: 15px; border-radius: 8px; border-left: 5px solid #17a2b8; box-shadow: 0 2px 4px rgba(0,0,0,0.05);">
                                    <div style="font-size: 11px; color: #666; text-uppercase; font-weight: bold; margin-bottom: 5px;">Líneas Sinc.</div>
                                    <div style="font-size: 16px; color: #17a2b8; font-weight: bold;">{len(remote_lines)}</div>
                                </td>
                            </tr>
                        </table>
                        <div style="text-align: right; padding: 10px; font-size: 11px; color: #999; font-style: italic;">
                            Sincronizado el: {fields.Datetime.now()}
                        </div>
                    </div>
                """
                
                order.write({
                    'is_synced': True,
                    'remote_order_ref': remote_ref,
                    'sync_log': log_html
                })

                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Sincronización exitosa'),
                        'message': f'Pedido creado en remoto: {remote_ref}',
                        'type': 'success',
                    }
                }

            except Exception as e:
                raise UserError(_('Error al sincronizar: %s') % str(e))
