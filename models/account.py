# -*- encoding: utf-8 -*-

from odoo import models, fields, api, tools, _
from odoo.exceptions import UserError, ValidationError

import requests
import logging
import json

class AccountMove(models.Model):
    _inherit = "account.move"

    pdf_fel_sv = fields.Char('PDF FEL SV', copy=False)

    def _post(self, soft=True):
        if self.certificar_sv():
            return super(AccountMove, self)._post(soft)

    def post(self):
        if self.certificar_sv():
            return super(AccountMove, self).post()

    def formato_float(self, valor, redondeo):
        return float('{:.6f}'.format(tools.float_round(valor, precision_digits=redondeo)))

    def certificar_sv(self):
        for factura in self:
            if factura.requiere_certificacion_sv():
                self.ensure_one()

                if factura.error_pre_validacion_sv():
                    return False
                #### DATOS GENERALES #######
                tipo_documento = factura.journal_id.tipo_documento_fel_sv.zfill(2)
                ####
                ##### RECEPTOR/ CLIENTE ####
                tipo_doc_recep = factura.partner_id.tipo_documento_fel
                num_documeto = factura.partner_id.vat
                nombre= factura.partner_id.name
                correo = factura.partner_id.email
                tel = factura.partner_id.phone or factura.partner_id.mobile
                nrc = factura.partner_id.numero_registro
                codigo_actividad = factura.partner_id.codigo_actividad
                nombre_comercial = factura.partner_id.nombre_comercial
                departamento = factura.partner_id.departamento_fel_sv
                municipio = factura.partner_id.municipio_fel_sv
                es_exento = factura.partner_id.exento_iva
                ###### FIN DATOS GENERALES ####

                ######## CONDCION/FORMA DE PAGO PUEDE SER CONTADO, CREDITO, OTRO, TOMA PRIMERO EL DE LA FACTURA, SINO EL DEL DIARIO
                condicion_pago_fel_sv = 1
                forma_pago_fel_sv = factura.forma_pago_fel_sv or factura.journal_id.forma_pago_fel_sv
                lineas_plazo_pago = factura.invoice_payment_term_id.line_ids
                for line in lineas_plazo_pago:
                    print(line.days,"days!!!!")
                    if line.days:
                        plazo_credito = '01'
                        periodo_credito = line.days
                        condicion_pago_fel_sv = 2
                    if line.months:
                        plazo_credito = '02'
                        periodo_credito = line.months
                        condicion_pago_fel_sv = 2
                ######### NUMERO DE CONTROL #####################
                sequence_obj = factura.journal_id.sequence_id

                if not sequence_obj:
                    raise UserError("No se ha configurado una secuencia fel para este diario.")

                next_number = sequence_obj.number_next_actual
                sequencia = str(next_number).zfill(15)
                numero_control_fel = f"DTE-{tipo_documento}-{factura.company_id.establecimiento_sv}P001-{sequencia}"
                factura.numero_control = numero_control_fel
                mumero_control = numero_control_fel
                ############ FIN NUMERO DE CONTROL ###########
                ######### DOCUMENTO ##########
                factura_json = { 'documento': {
                    'tipo_dte': tipo_documento,
                    'establecimiento': factura.company_id.establecimiento_sv,
                    'condicion_pago': condicion_pago_fel_sv,
                    'numero_control': mumero_control,
                }}
                # SI ES DE TIPO EXPORTACION AGREGAMOS
                if tipo_documento == '11':
                    factura_json['documento']['tipo_item_exportacion'] = int(factura.tipo_item_exportacion)
                    # SOLO SI EXPORTAN BIENES
                    # factura_json['documento']['recinto_fiscal'] = factura.company_id.recinto_fiscal
                    # factura_json['documento']['regimen'] = factura.company_id.regimen
                    # factura_json['documento']['codigo_incoterm'] = factura.company_id.codigo_incoterm
                ######### FIN DOCUMENTO #########



                incluir_impuestos = True
                ####  RECEPTOR #################
                if tipo_documento in ['01','11','14']:
                    receptor = {
                        "tipo_documento": tipo_doc_recep,
                        "numero_documento":  num_documeto,
                        'nombre': nombre,
                        "correo": correo,
                        "telefono": tel

                    }
                    ## SI ES DE EXPORTACION
                    if tipo_documento == '11':
                        receptor['descripcion_actividad'] = factura.partner_id.descripcion_actividad
                        receptor['nombre_comercial'] = factura.partner_id.nombre_comercial
                        receptor['codigo_pais'] = factura.partner_id.codigo_pais
                        receptor['complemento'] = factura.partner_id.street or ''
                        receptor['tipo_persona'] = int(factura.partner_id.tipo_persona)


                    if tipo_documento != '14':
                        factura_json['documento']['receptor'] = receptor
                    # SI ES FACTRUA SUEJTO EXCLUIDA CAMBIAMOS EL NOMBRE DE LA LLAVE Y AGREGAMOS LA DIRECCION
                    else:
                        receptor['direccion'] = {
                            'departamento': departamento,
                            'municipio': municipio,
                            'complemento': factura.partner_id.street or ''
                        }
                        factura_json['documento']['sujeto_excluido'] = receptor

                elif tipo_documento in ['03', '04', '05']:
                    # SI ES NOTA DE CREDITO AGREGAMOS EL DOC RELACIONADO
                    if tipo_documento == '05':
                        doc_relacionados = [{
                            "tipo_documento": "03",
                            "tipo_generacion": 2,
                            "numero_documento": factura.firma_fel_sv,
                            "fecha_emision": str(factura.date)
                      }]
                        factura_json['documento']['documentos_relacionados'] = doc_relacionados

                    incluir_impuestos = False
                    receptor = {
                        'tipo_documento': tipo_doc_recep,
                         "numero_documento":  num_documeto,
                        'nrc': nrc,
                        'nombre': nombre,
                        'codigo_actividad': codigo_actividad,
                        'nombre_comercial': nombre_comercial,
                        'correo': correo,
                        'direccion': {
                            'departamento': departamento,
                            'municipio': municipio,
                            'complemento': factura.partner_id.street or '',
                        },
                        'telefono': tel,
                    }

                    factura_json['documento']['receptor'] = receptor
                ########### FIN EXTRUCTURACION DE RECEPTOR ################

                ###### ITEMS ###############
                items = [];
                for linea in factura.invoice_line_ids:
                    impuestos = 0
                    r = linea.tax_ids.compute_all(linea.price_unit, currency=factura.currency_id, quantity=1,
                                                  product=linea.product_id, partner=factura.partner_id)
                    precio_unitario = linea.price_unit
                    precio_unitario_fel = r['total_included']
                    if not incluir_impuestos and len(linea.tax_ids) > 0:
                        precio_unitario_fel = r['total_excluded']
                        # Para calcular los impuestos, es necesario quitar el descuento y tomar en cuenta todas las cantidades
                        impuestos = (r['total_included'] - r['total_excluded']) * linea.quantity
                        print(impuestos,r['total_included'],r['total_excluded'],"ssssss")
                    monto_descuento = (linea.discount / 100) * (precio_unitario * linea.quantity)
                    item = {
                        'cantidad': int(linea.quantity),
                        'unidad_medida': int(linea.product_id.codigo_unidad_medida_fel_sv) or 59,
                        'descuento': self.formato_float(monto_descuento, 4),
                        'descripcion': linea.name,
                        'precio_unitario': self.formato_float(precio_unitario_fel, 4),
                    }
                    # AGREGAMOS TIPO DE VENTA SI ES FACTURA/ COMPROBANTE FISCAL NOTA DE CREDITO
                    if tipo_documento  in ['01','03','05']:
                        item['tipo_venta'] = "3" if es_exento else "1"
                        if es_exento:
                            # SI INCLUIR IMPUESTOS SIGINIFCA NO DESGLOSAR LOS IMPUESTOS PARA EL TRIBUTO
                            incluir_impuestos = True

                    if tipo_documento != '11':
                        # AGREGAMOS TIPO SI ES DIFERENTE A FACTURA DE EXPORTACION, PARA EXP NO VA
                        item['tipo'] = 1 if linea.product_id.type != 'service' else 2
                    # SI ES NOTA DE CREDTIO AGREGAMOS EL DOC RELACIONADO
                    if tipo_documento == '05':
                        item['numero_documento'] = factura.firma_fel_sv
                    # SINO INCLUIMOS EL IMPUESTO EN EL TOTAL QUIERE DECIR QUE HAY QUE DESGLOSAR EL IMPUESTO
                    # ESTO ES PARA COMPROBANTE FISCAL
                    if not incluir_impuestos:
                        item['tributos'] = [{ 'codigo': '20', 'monto': self.formato_float(impuestos, 4) }]

                    items.append(item)

                    ############# FIN ITEMS
                factura_json['documento']['items'] = items
                ############################ PAGOS
                factura_json['documento']['pagos'] = \
                    [
                        {'tipo': forma_pago_fel_sv,
                         'monto': self.formato_float(factura.amount_total, 4)
                         }
                    ]
                # SI CONDICION DE TIPO CREDITO
                if condicion_pago_fel_sv == 2:
                    #### CONSULTAR CATOLOGO
                    factura_json['documento']['pagos'][0].update({
                        "plazo": plazo_credito,
                        "periodo": periodo_credito
                    })
                ########################### FIN PAGOS

                headers = {
                    "Content-Type": "application/json",
                    "usuario": factura.company_id.usuario_fel_sv,
                    "llave": factura.company_id.llave_fel_sv,
                    "identificador": factura.journal_id.code+str(factura.id),
                }
                print(factura_json,"factura json!!!")
                url = 'https://sandbox-certificador.infile.com.sv/api/v1/certificacion/test/documento/certificar'
                if factura.company_id.pruebas_fel_sv:
                    url = 'https://certificador.infile.com.sv/api/v1/certificacion/test/documento/certificar'
                print(headers,factura_json)
                r = requests.post(url, json=factura_json, headers=headers)

                logging.warning(r.text,"texttt!!!")
                certificacion_json = r.json()
                if certificacion_json["ok"]:
                    factura.firma_fel_sv = certificacion_json["respuesta"]["codigoGeneracion"]
                    factura.sello_recepcion = certificacion_json["respuesta"]["selloRecepcion"]
                    factura.pdf_fel_sv = certificacion_json["pdf_path"]
                    factura.tipo_documento_fel_sv = factura.journal_id.tipo_documento_fel_sv
                    sequence_obj.next_by_id() or '1'

                else:
                    factura.numero_control = ""
                    if certificacion_json["errores"]:
                        factura.error_certificador_sv(str(certificacion_json["errores"])+ f' Numero Control: {mumero_control}')




        return True

    def button_cancel(self):

        result = super(AccountMove, self).button_cancel()
        for factura in self:
            if factura.firma_fel_sv:
                invalidacion_json = { 'invalidacion': {
                    'establecimiento': factura.company_id.establecimiento_sv,
                    'uuid': factura.firma_fel_sv,
                    'tipo_anulacion': int(factura.tipo_anulacion_fel_sv),
                    'motivo': factura.motivo_fel_sv,
                    'responsable': {
                        'nombre': factura.responsable_fel_sv_id.name,
                        'tipo_documento': factura.responsable_fel_sv_id.tipo_documento_fel,
                        'numero_documento': factura.responsable_fel_sv_id.vat,
                    },
                    'solicitante': {
                        'nombre': factura.solicitante_fel_sv_id.name,
                        'tipo_documento': factura.solicitante_fel_sv_id.tipo_documento_fel,
                        'numero_documento': factura.solicitante_fel_sv_id.vat,
                        'correo': factura.solicitante_fel_sv_id.email,
                    }
                }}

                if int(factura.tipo_anulacion_fel_sv) ==1:
                    invalidacion_json['invalidacion']['nuevo_documento'] = factura.factura_nueva_fel_sv_id.firma_fel_sv


                headers = {
                    "Content-Type": "application/json",
                    "usuario": factura.company_id.usuario_fel_sv,
                    "llave": factura.company_id.llave_fel_sv,
                }
                url = 'https://sandbox-certificador.infile.com.sv/api/v1/certificacion/test/documento/invalidacion'
                if factura.company_id.pruebas_fel_sv:
                    url = 'https://certificador.infile.com.sv/api/v1/certificacion/test/documento/invalidacion'
                print(invalidacion_json,"json")
                r = requests.post(url, json=invalidacion_json, headers=headers)

                logging.warning(r.text)
                certificacion_json = r.json()

                if certificacion_json["ok"]:
                    factura.firma_fel_sv = certificacion_json["respuesta"]["codigoGeneracion"]
                    factura.sello_recepcion = certificacion_json["respuesta"]["selloRecepcion"]
                    factura.pdf_fel_sv = certificacion_json["pdf_path"]



                else:
                    raise UserError(str(certificacion_json["errores"]))

class ResCompany(models.Model):
    _inherit = "res.company"

    usuario_fel_sv = fields.Char('Usuario FEL SV')
    llave_fel_sv = fields.Char('Clave FEL SV')
    certificador_fel_sv = fields.Selection(selection_add=[('infile_sv', 'Infile SV')])
    pruebas_fel_sv = fields.Boolean('Pruebas FEL SV')
