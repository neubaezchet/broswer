"""Mapeo automático de arquitectura de portales."""

import json
import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from models.portals import Portal, Formulario, Endpoint

logger = logging.getLogger(__name__)


async def map_portal(
    page,
    portal_name: str,
    db: Session,
) -> Dict[str, Any]:
    """
    Extrae TODA la arquitectura del portal después del login.
    
    Guarda:
    - Formularios y sus campos
    - Navegación
    - Botones
    - URLs de acciones
    """
    
    logger.info(f"🗺️ Mapeando portal: {portal_name}")
    
    try:
        structure = await page.evaluate("""
        () => {
            const forms = Array.from(document.forms).map(form => ({
                id: form.id || form.name,
                name: form.name,
                action: form.action,
                method: form.method,
                fields: Array.from(form.elements).map(field => ({
                    name: field.name,
                    type: field.type,
                    id: field.id,
                    required: field.required,
                    placeholder: field.placeholder || '',
                    value: field.value || '',
                    options: field.tagName === 'SELECT' 
                        ? Array.from(field.options).map(o => ({text: o.text, value: o.value}))
                        : null,
                    readonly: field.readOnly,
                    disabled: field.disabled,
                }))
            }));
            
            const navigation = Array.from(document.querySelectorAll('nav a, .nav a, [role="navigation"] a, .menu a, .sidebar a'))
                .map(a => ({
                    text: a.textContent.trim(),
                    href: a.href,
                    title: a.title,
                }))
                .filter(n => n.text.length > 0);
            
            const buttons = Array.from(document.querySelectorAll('button, input[type="button"], input[type="submit"]'))
                .map(b => ({
                    text: b.textContent.trim() || b.value,
                    id: b.id,
                    name: b.name,
                    type: b.type,
                    class: b.className,
                }))
                .filter(b => b.text.length > 0);
            
            return {
                forms,
                navigation,
                buttons,
                title: document.title,
                url: window.location.href,
                timestamp: new Date().toISOString(),
            };
        }
        """)
        
        logger.info(f"✅ Estructura extraída: {len(structure['forms'])} formularios, {len(structure['navigation'])} rutas")
        
        # Guardar en BD
        portal = db.query(Portal).filter_by(nombre=portal_name).first()
        if not portal:
            portal = Portal(nombre=portal_name, url=structure['url'], estructura_json=structure)
            db.add(portal)
        else:
            portal.estructura_json = structure
            portal.url = structure['url']
        
        db.commit()
        logger.info(f"💾 Portal {portal_name} guardado en BD")
        
        return structure
        
    except Exception as e:
        logger.error(f"❌ Error mapeando portal: {e}")
        return None


async def load_portal_map(portal_name: str, db: Session) -> Optional[Dict[str, Any]]:
    """Carga la arquitectura de un portal de BD si ya fue mapeado."""
    
    portal = db.query(Portal).filter_by(nombre=portal_name).first()
    if portal and portal.estructura_json:
        logger.info(f"📦 Usando mapa guardado de {portal_name}")
        return portal.estructura_json
    
    return None


async def get_form_by_name(portal_name: str, form_name: str, db: Session) -> Optional[Dict[str, Any]]:
    """Obtiene un formulario específico de un portal mapeado."""
    
    portal = db.query(Portal).filter_by(nombre=portal_name).first()
    if portal and portal.estructura_json:
        for form in portal.estructura_json.get('forms', []):
            if form_name.lower() in form.get('name', '').lower():
                return form
    
    return None


async def detect_api_endpoint(page, button_id: str) -> Optional[Dict[str, str]]:
    """Detecta qué API llama un botón sin hacer click real."""
    
    try:
        result = await page.evaluate(f"""
        () => {{
            const btn = document.getElementById('{button_id}');
            if (!btn) return null;
            
            return {{
                onclick: btn.getAttribute('onclick'),
                dataAction: btn.dataset.action,
                dataTarget: btn.dataset.target,
                formId: btn.form?.id,
                formAction: btn.form?.action,
                type: btn.type,
                class: btn.className,
            }};
        }}
        """)
        
        logger.info(f"🔍 Endpoint detectado para botón {button_id}: {result}")
        return result
        
    except Exception as e:
        logger.warning(f"⚠️ No se pudo detectar endpoint: {e}")
        return None
