from __future__ import annotations


def _gn_patch_diagnostic(severity, code, path, message):
    return {
        "severity": severity,
        "code": code,
        "path": path,
        "message": message,
    }

def _gn_rna_property(owner, identifier):
    try:
        return owner.bl_rna.properties.get(identifier)
    except (AttributeError, KeyError, TypeError):
        return None
