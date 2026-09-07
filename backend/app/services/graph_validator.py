import logging
from typing import Any

logger = logging.getLogger(__name__)

FORBIDDEN_NODE_TYPES = {
    "test", "test_case", "test_suite", "test_script", "playwright_test",
    "test_file", "unit_test", "e2e_test", "integration_test"
}

VALID_NODE_TYPES = {
    "project", "directory", "file", "module", "class", "function",
    "method", "component", "route", "database_operation", "package"
}

VALID_RELATIONSHIP_TYPES = {
    "imports", "exports", "calls", "references", "extends", "implements",
    "renders", "routes_to", "handles", "uses", "contains"
}

def extract_code_map_entities(code_map: dict) -> set:
    """Extract all known entity names/paths from the code map for validation."""
    entities = set()
    raw_files = code_map.get("files", {})
    if isinstance(raw_files, dict):
        items = list(raw_files.items())
    elif isinstance(raw_files, list):
        items = [("", f) for f in raw_files]
    else:
        items = []

    for path_key, f in items:
        file_info = f.get("file", {}) if isinstance(f.get("file"), dict) else {}
        rel_p = path_key or f.get("relativePath") or file_info.get("relativePath", "")
        if rel_p:
            entities.add(rel_p)
            base_name = rel_p.replace('\\', '/').split('/')[-1]
            if base_name:
                entities.add(base_name)
        name = f.get("name") or file_info.get("name", "")
        if name:
            entities.add(name)
        for sym in f.get("symbols", []):
            entities.add(sym.get("name", ""))
        for decl in f.get("declarations", []):
            entities.add(decl.get("name", ""))
        for cls in f.get("classes", []):
            entities.add(cls.get("name", ""))
        for imp in f.get("imports", []):
            src = imp.get("source") or imp.get("modulePath")
            if src:
                entities.add(src)
        for dep in f.get("dependencies", []):
            src = dep.get("target") or dep.get("source")
            if src:
                entities.add(src)
    return {e for e in entities if e}

def extract_code_map_imports(code_map: dict) -> set:
    """Extract all direct external or module import sources from application files."""
    imports = set()
    raw_files = code_map.get("files", {})
    files_list = raw_files.values() if isinstance(raw_files, dict) else (raw_files if isinstance(raw_files, list) else [])
    for f in files_list:
        for imp in f.get("imports", []):
            src = imp.get("source") or imp.get("modulePath")
            if src:
                src_clean = src.strip()
                imports.add(src_clean)
                imports.add(f"module:{src_clean}")
                imports.add(f"package:{src_clean}")
                imports.add(f"file:{src_clean}")
        for dep in f.get("dependencies", []):
            src = dep.get("target") or dep.get("source")
            if src:
                src_clean = src.strip()
                imports.add(src_clean)
                imports.add(f"module:{src_clean}")
                imports.add(f"package:{src_clean}")
                imports.add(f"file:{src_clean}")
    return imports

def validate_graph(raw_graph: dict, code_map: dict) -> dict:
    """Validate and clean the LLM-generated graph against the Code Map."""
    nodes = raw_graph.get("nodes", [])
    edges = raw_graph.get("edges", [])
    
    code_map_imports = extract_code_map_imports(code_map)
    
    # 1. Validate + deduplicate nodes
    seen_node_ids = set()
    valid_nodes = []
    for node in nodes:
        node_id = node.get("id", "").strip()
        node_type = node.get("type", "").strip().lower()
        node_name = node.get("name", "").strip()
        
        if not node_id:
            logger.warning(f"Skipping node with no ID: {node}")
            continue
        if node_id in seen_node_ids:
            logger.warning(f"Duplicate node ID skipped: {node_id}")
            continue
        
        # Explicitly reject test node types
        if node_type in FORBIDDEN_NODE_TYPES or "test" in node_type:
            logger.warning(f"Rejecting test node type '{node_type}' for node {node_id}")
            continue

        # Reject test case IDs or test files that slipped through
        if node_id.startswith("tc_") or "spec." in node_id.lower() or "test." in node_id.lower():
            logger.warning(f"Rejecting test-named node: {node_id}")
            continue

        if node_type not in VALID_NODE_TYPES:
            logger.warning(f"Invalid node type '{node_type}' for node {node_id}, defaulting to 'module'")
            node_type = "module"
            
        if not node_name:
            logger.warning(f"Node {node_id} has no name, skipping")
            continue
        
        # If node is an external package/module, verify it was directly imported by application files
        if node_type in ("package", "module") and not node.get("filePath"):
            is_imported = (
                node_id in code_map_imports or
                node_name in code_map_imports or
                any(src in node_id or src in node_name for src in code_map_imports)
            )
            if not is_imported:
                logger.warning(f"Skipping external package node '{node_id}' because it was not imported by any application file")
                continue

        seen_node_ids.add(node_id)
        valid_nodes.append({
            "id": node_id,
            "type": node_type,
            "name": node_name,
            "filePath": node.get("filePath"),
            "location": node.get("location"),
            "metadata": node.get("metadata", {})
        })
    
    logger.info(f"Valid nodes: {len(valid_nodes)} / {len(nodes)}")
    
    # 2. Validate + deduplicate edges
    seen_edges = set()
    valid_edges = []
    for edge in edges:
        source = edge.get("source", "").strip()
        target = edge.get("target", "").strip()
        relationship = edge.get("relationship", "").strip()
        evidence = edge.get("evidence", {})
        edge_id = edge.get("id", f"{source}->{target}:{relationship}")
        
        if not source or not target or not relationship:
            logger.warning(f"Edge missing required fields: {edge}")
            continue
        if source not in seen_node_ids:
            logger.warning(f"Edge source '{source}' not in valid nodes, skipping")
            continue
        if target not in seen_node_ids:
            logger.warning(f"Edge target '{target}' not in valid nodes, skipping")
            continue
        if relationship not in VALID_RELATIONSHIP_TYPES:
            logger.warning(f"Invalid relationship type '{relationship}', skipping edge {edge_id}")
            continue
        if not evidence:
            logger.warning(f"Edge {edge_id} has no evidence, skipping")
            continue
        
        dedup_key = f"{source}->{target}:{relationship}"
        if dedup_key in seen_edges:
            logger.warning(f"Duplicate edge skipped: {dedup_key}")
            continue
        
        seen_edges.add(dedup_key)
        valid_edges.append({
            "id": edge_id,
            "source": source,
            "target": target,
            "relationship": relationship,
            "confidence": float(edge.get("confidence", 1.0)),
            "evidence": evidence,
            "reason": edge.get("reason", "")
        })
    
    logger.info(f"Valid edges: {len(valid_edges)} / {len(edges)}")
    return {"nodes": valid_nodes, "edges": valid_edges}
