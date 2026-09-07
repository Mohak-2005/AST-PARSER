/**
 * Language-Independent Core AST Extractor with Language-Specific AST Handlers.
 *
 * Supports Java, Python, JavaScript, TypeScript, TSX, C, C++, C#, Go, Rust, etc.,
 * with a resilient generic fallback for any other Tree-sitter grammar.
 */

function extractInfo(fileInfo, parseResult) {
  const result = {
    file: {
      path: fileInfo.absolutePath,
      relativePath: fileInfo.relativePath,
      name: fileInfo.fileName,
      extension: fileInfo.extension,
      language: parseResult.language,
      lineCount: parseResult.sourceCode ? parseResult.sourceCode.split('\n').length : 0
    },
    parse: {
      status: parseResult.hasErrors ? 'error' : 'success',
      errors: parseResult.errors || []
    },
    symbols: [],
    imports: [],
    exports: [],
    calls: [],
    references: [],
    classes: [],
    react: { components: [], jsxElements: [] },
    routes: [],
    databaseOperations: []
  };

  if (!parseResult.tree) {
    return result;
  }

  const sourceCode = parseResult.sourceCode || '';
  const language = parseResult.language || '';

  function getLocation(node) {
    return {
      startLine: node.startPosition.row + 1,
      startColumn: node.startPosition.column,
      endLine: node.endPosition.row + 1,
      endColumn: node.endPosition.column
    };
  }

  function getNodeText(node) {
    if (!node) return null;
    return sourceCode.substring(node.startIndex, node.endIndex);
  }

  function findChildOfType(node, type) {
    for (let i = 0; i < node.childCount; i++) {
      const child = node.child(i);
      if (child.type === type) return child;
    }
    return null;
  }

  function findChildrenOfType(node, type) {
    const list = [];
    for (let i = 0; i < node.childCount; i++) {
      const child = node.child(i);
      if (child.type === type) list.push(child);
    }
    return list;
  }

  // -------------------------------------------------------------
  // Language Specific Handlers
  // -------------------------------------------------------------

  function handleJava(node, loc, type) {
    // 1. Package declaration
    if (type === 'package_declaration') {
      const pkgIdentifier = node.children.find(c => c.type === 'scoped_identifier' || c.type === 'identifier');
      if (pkgIdentifier) {
        result.symbols.push({
          type: 'package',
          name: getNodeText(pkgIdentifier),
          location: loc
        });
      }
      return;
    }

    // 2. Import declaration: import java.util.*; or import com.example.Card;
    if (type === 'import_declaration') {
      const nameNode = node.children.find(c => c.type === 'scoped_identifier' || c.type === 'identifier');
      const asterisk = node.children.some(c => c.type === 'asterisk');
      let importSource = nameNode ? getNodeText(nameNode) : '';
      if (asterisk && !importSource.endsWith('*')) {
        importSource = importSource ? `${importSource}.*` : '*';
      }

      if (importSource) {
        result.imports.push({
          source: importSource,
          specifiers: [{ type: asterisk ? 'namespace' : 'named', name: importSource }],
          location: loc,
          rawStatement: getNodeText(node).trim()
        });
      }
      return;
    }

    // 3. Class / Interface / Enum / Record declarations
    if (type === 'class_declaration' || type === 'interface_declaration' || type === 'enum_declaration' || type === 'record_declaration') {
      const nameNode = node.children.find(c => c.type === 'identifier');
      const className = nameNode ? getNodeText(nameNode) : 'Anonymous';

      const classInfo = {
        name: className,
        kind: type.replace('_declaration', ''),
        extends: null,
        implements: [],
        methods: [],
        location: loc
      };

      // Check extends (superclass)
      const superclassNode = findChildOfType(node, 'superclass');
      if (superclassNode) {
        const superType = superclassNode.children.find(c => c.type === 'type_identifier' || c.type === 'scoped_type_identifier');
        if (superType) classInfo.extends = getNodeText(superType);
      }

      // Check implements (super_interfaces)
      const superInterfacesNode = findChildOfType(node, 'super_interfaces');
      if (superInterfacesNode) {
        const ifaceList = findChildOfType(superInterfacesNode, 'interface_type_list');
        const types = ifaceList ? ifaceList.children.filter(c => c.type === 'type_identifier') : [];
        types.forEach(t => classInfo.implements.push(getNodeText(t)));
      }

      // Class body methods
      const body = findChildOfType(node, 'class_body') || findChildOfType(node, 'interface_body') || findChildOfType(node, 'enum_body');
      if (body) {
        for (let i = 0; i < body.childCount; i++) {
          const m = body.child(i);
          if (m.type === 'method_declaration' || m.type === 'constructor_declaration') {
            const mNameNode = m.children.find(c => c.type === 'identifier');
            if (mNameNode) {
              classInfo.methods.push(getNodeText(mNameNode));
            }
          }
        }
      }

      result.classes.push(classInfo);
      result.symbols.push({
        type: 'class',
        name: className,
        location: loc
      });
      return;
    }

    // 4. Method / Constructor declarations
    if (type === 'method_declaration' || type === 'constructor_declaration') {
      const nameNode = node.children.find(c => c.type === 'identifier');
      if (nameNode) {
        result.symbols.push({
          type: type === 'constructor_declaration' ? 'constructor' : 'method',
          name: getNodeText(nameNode),
          location: loc
        });
      }
      return;
    }

    // 5. Object Creation: new Card(suit, rank) -> Instantiation relationship
    if (type === 'object_creation_expression') {
      const typeNode = node.children.find(c => c.type === 'type_identifier' || c.type === 'scoped_type_identifier');
      if (typeNode) {
        const instantiatedClass = getNodeText(typeNode);
        result.calls.push({
          callee: `new ${instantiatedClass}`,
          targetClass: instantiatedClass,
          kind: 'instantiation',
          location: loc
        });
        result.references.push({
          symbol: instantiatedClass,
          kind: 'type_reference',
          location: loc
        });
      }
      return;
    }

    // 6. Method invocation: cards.add(...), Collections.shuffle(...)
    if (type === 'method_invocation') {
      const objNode = node.childForFieldName ? node.childForFieldName('object') : null;
      const nameNode = node.childForFieldName ? node.childForFieldName('name') : null;
      const methodName = nameNode ? getNodeText(nameNode) : (node.children.find(c => c.type === 'identifier') ? getNodeText(node.children.find(c => c.type === 'identifier')) : getNodeText(node));
      const fullCall = objNode ? `${getNodeText(objNode)}.${methodName}` : methodName;

      result.calls.push({
        callee: fullCall,
        methodName: methodName,
        location: loc
      });
      return;
    }
  }

  function handleCppAndC(node, loc, type) {
    // #include <iostream> or #include "calculator.h"
    if (type === 'preproc_include') {
      const pathNode = node.children.find(c => c.type === 'string_literal' || c.type === 'system_lib_string');
      if (pathNode) {
        const text = getNodeText(pathNode);
        const cleanPath = text.replace(/^[<"]|[>"]$/g, '');
        const isSystem = text.startsWith('<');
        result.imports.push({
          source: cleanPath,
          specifiers: [{ type: isSystem ? 'system_include' : 'local_include', name: cleanPath }],
          location: loc,
          rawStatement: getNodeText(node).trim()
        });
      }
      return;
    }

    // Function definition
    if (type === 'function_definition') {
      const declarator = findChildOfType(node, 'function_declarator');
      if (declarator) {
        const idNode = declarator.children.find(c => c.type === 'identifier' || c.type === 'field_identifier');
        if (idNode) {
          result.symbols.push({
            type: 'function',
            name: getNodeText(idNode),
            location: loc
          });
        }
      }
      return;
    }

    // Class / Struct
    if (type === 'class_specifier' || type === 'struct_specifier') {
      const nameNode = node.children.find(c => c.type === 'type_identifier');
      if (nameNode) {
        const className = getNodeText(nameNode);
        result.classes.push({
          name: className,
          kind: type.includes('struct') ? 'struct' : 'class',
          methods: [],
          location: loc
        });
        result.symbols.push({
          type: 'class',
          name: className,
          location: loc
        });
      }
      return;
    }

    // Calls
    if (type === 'call_expression') {
      const funcNode = node.child(0);
      if (funcNode) {
        result.calls.push({
          callee: getNodeText(funcNode),
          location: loc
        });
      }
      return;
    }
  }

  function handleGo(node, loc, type) {
    // import (...) or import "fmt"
    if (type === 'import_declaration') {
      const specs = findChildrenOfType(node, 'import_spec');
      if (specs.length > 0) {
        specs.forEach(spec => {
          const pathNode = findChildOfType(spec, 'interpreted_string_literal');
          if (pathNode) {
            const mod = getNodeText(pathNode).replace(/"/g, '');
            result.imports.push({ source: mod, specifiers: [{ name: mod }], location: loc });
          }
        });
      } else {
        const pathNode = findChildOfType(node, 'interpreted_string_literal');
        if (pathNode) {
          const mod = getNodeText(pathNode).replace(/"/g, '');
          result.imports.push({ source: mod, specifiers: [{ name: mod }], location: loc });
        }
      }
      return;
    }

    if (type === 'function_declaration' || type === 'method_declaration') {
      const nameNode = findChildOfType(node, 'identifier') || findChildOfType(node, 'field_identifier');
      if (nameNode) {
        result.symbols.push({
          type: type === 'method_declaration' ? 'method' : 'function',
          name: getNodeText(nameNode),
          location: loc
        });
      }
      return;
    }

    if (type === 'call_expression') {
      const funcNode = node.child(0);
      if (funcNode) result.calls.push({ callee: getNodeText(funcNode), location: loc });
      return;
    }
  }

  function handleRust(node, loc, type) {
    if (type === 'use_declaration') {
      result.imports.push({
        source: getNodeText(node).replace(/^use\s+|;$/g, '').trim(),
        specifiers: [],
        location: loc
      });
      return;
    }
    if (type === 'function_item') {
      const nameNode = findChildOfType(node, 'identifier');
      if (nameNode) {
        result.symbols.push({ type: 'function', name: getNodeText(nameNode), location: loc });
      }
      return;
    }
    if (type === 'struct_item' || type === 'enum_item') {
      const nameNode = findChildOfType(node, 'type_identifier');
      if (nameNode) {
        result.classes.push({ name: getNodeText(nameNode), methods: [], location: loc });
        result.symbols.push({ type: 'class', name: getNodeText(nameNode), location: loc });
      }
      return;
    }
    if (type === 'call_expression') {
      const funcNode = node.child(0);
      if (funcNode) result.calls.push({ callee: getNodeText(funcNode), location: loc });
      return;
    }
  }

  function handleCSharp(node, loc, type) {
    if (type === 'using_directive') {
      const nameNode = node.children.find(c => c.type === 'qualified_name' || c.type === 'identifier');
      if (nameNode) {
        result.imports.push({ source: getNodeText(nameNode), specifiers: [], location: loc });
      }
      return;
    }
    if (type === 'class_declaration' || type === 'interface_declaration') {
      const nameNode = findChildOfType(node, 'identifier');
      if (nameNode) {
        result.classes.push({ name: getNodeText(nameNode), methods: [], location: loc });
        result.symbols.push({ type: 'class', name: getNodeText(nameNode), location: loc });
      }
      return;
    }
    if (type === 'method_declaration') {
      const nameNode = findChildOfType(node, 'identifier');
      if (nameNode) result.symbols.push({ type: 'method', name: getNodeText(nameNode), location: loc });
      return;
    }
    if (type === 'invocation_expression') {
      const expr = node.child(0);
      if (expr) result.calls.push({ callee: getNodeText(expr), location: loc });
      return;
    }
  }

  // -------------------------------------------------------------
  // Coordinated AST Traversal
  // -------------------------------------------------------------

  function traverse(node) {
    if (!node) return;

    const loc = getLocation(node);
    const type = node.type;

    // Delegate to language-specific handler if applicable
    if (language === 'java') {
      handleJava(node, loc, type);
    } else if (language === 'c' || language === 'cpp') {
      handleCppAndC(node, loc, type);
    } else if (language === 'go') {
      handleGo(node, loc, type);
    } else if (language === 'rust') {
      handleRust(node, loc, type);
    } else if (language === 'c_sharp') {
      handleCSharp(node, loc, type);
    }

    // -----------------------------------------------------------
    // Common JS / TS / Python / Generic Fallback Handlers
    // -----------------------------------------------------------

    // Imports (JS/TS import_declaration, Python import_statement)
    if (type === 'import_statement' || type === 'import_declaration') {
      const sourceNode = node.children.find(c => c.type === 'string');
      if (sourceNode) {
        // JS/TS style: import { x } from 'y'
        const source = getNodeText(sourceNode).replace(/['"]/g, '');
        const importItem = {
          source,
          specifiers: [],
          location: loc
        };

        const clause = node.children.find(c => c.type === 'import_clause');
        if (clause) {
          clause.children.forEach(c => {
            if (c.type === 'identifier') {
              importItem.specifiers.push({ type: 'default', name: getNodeText(c) });
            } else if (c.type === 'named_imports') {
              c.children.forEach(spec => {
                if (spec.type === 'import_specifier') {
                  const nameNode = spec.children.find(n => n.type === 'identifier');
                  if (nameNode) importItem.specifiers.push({ type: 'named', name: getNodeText(nameNode) });
                }
              });
            } else if (c.type === 'namespace_import') {
              const idNode = c.children.find(n => n.type === 'identifier');
              if (idNode) importItem.specifiers.push({ type: 'namespace', name: getNodeText(idNode) });
            }
          });
        }
        result.imports.push(importItem);
      } else if (language === 'python') {
        // Python style: import mysql.connector, import os, sys
        node.children.forEach(c => {
          if (c.type === 'dotted_name') {
            const modName = getNodeText(c);
            result.imports.push({
              source: modName,
              specifiers: [{ type: 'namespace', name: modName }],
              location: loc
            });
          } else if (c.type === 'aliased_import') {
            const nameNode = c.children.find(n => n.type === 'dotted_name' || n.type === 'identifier');
            const aliasNode = c.children.find(n => n.type === 'identifier' && n !== nameNode);
            if (nameNode) {
              const modName = getNodeText(nameNode);
              result.imports.push({
                source: modName,
                specifiers: [{ type: 'named', name: aliasNode ? getNodeText(aliasNode) : modName }],
                location: loc
              });
            }
          }
        });
      }
    }

    // Python from-import: from flask import Flask, render_template
    if (type === 'import_from_statement') {
      const modNode = node.children.find(c => c.type === 'dotted_name' || c.type === 'relative_import');
      const source = modNode ? getNodeText(modNode) : '';
      const importItem = { source, specifiers: [], location: loc };
      node.children.forEach(c => {
        if (c.type === 'dotted_name' && c !== modNode) {
          importItem.specifiers.push({ type: 'named', name: getNodeText(c) });
        } else if (c.type === 'aliased_import') {
          const nameNode = c.children.find(n => n.type === 'dotted_name' || n.type === 'identifier');
          if (nameNode) importItem.specifiers.push({ type: 'named', name: getNodeText(nameNode) });
        }
      });
      result.imports.push(importItem);
    }

    // Exports (JS/TS)
    if (type === 'export_statement' || type === 'export_default_declaration') {
      result.exports.push({
        type: type === 'export_default_declaration' ? 'default' : 'named',
        text: getNodeText(node).substring(0, 50) + '...',
        location: loc
      });
    }

    // Functions (JS function_declaration, method_definition, Python function_definition)
    if (language !== 'java' && (type === 'function_declaration' || type === 'method_definition' || type === 'function_definition')) {
      const nameNode = node.children.find(c => c.type === 'identifier' || c.type === 'property_identifier');
      if (nameNode) {
        result.symbols.push({
          type: type === 'method_definition' ? 'method' : 'function',
          name: getNodeText(nameNode),
          location: loc
        });
      }
    }

    // Arrow function (JS)
    if (type === 'arrow_function') {
      if (node.parent && node.parent.type === 'variable_declarator') {
        const nameNode = node.parent.children.find(c => c.type === 'identifier');
        if (nameNode) {
          result.symbols.push({
            type: 'function',
            name: getNodeText(nameNode),
            location: loc
          });
        }
      }
    }

    // Classes (JS class_declaration, Python class_definition)
    if (language !== 'java' && language !== 'c' && language !== 'cpp' && (type === 'class_declaration' || type === 'class_definition')) {
      const nameNode = node.children.find(c => c.type === 'identifier');
      const classInfo = {
        name: nameNode ? getNodeText(nameNode) : 'Anonymous',
        location: loc,
        methods: []
      };
      const body = node.children.find(c => c.type === 'class_body' || c.type === 'block');
      if (body) {
        body.children.forEach(c => {
          if (c.type === 'method_definition' || c.type === 'function_definition') {
            const mNameNode = c.children.find(n => n.type === 'property_identifier' || n.type === 'identifier');
            if (mNameNode) {
              classInfo.methods.push(getNodeText(mNameNode));
            }
          }
        });
      }
      result.classes.push(classInfo);
      result.symbols.push({
        type: 'class',
        name: classInfo.name,
        location: loc
      });
    }

    // Calls (JS call_expression, Python call)
    if (language !== 'java' && language !== 'c' && language !== 'cpp' && (type === 'call_expression' || type === 'call')) {
      const functionNode = node.children.find(c => c.type === 'identifier' || c.type === 'member_expression' || c.type === 'attribute');
      if (functionNode) {
        const callee = getNodeText(functionNode);

        // CommonJS require('...'), dynamic import('...'), Python __import__('...')
        if (callee === 'require' || callee === 'import' || callee === '__import__' || callee.endsWith('import_module')) {
          const argsNode = node.children.find(c => c.type === 'arguments' || c.type === 'argument_list');
          if (argsNode) {
            const firstArg = argsNode.children.find(c => c.type === 'string' || c.type === 'string_fragment' || c.type === 'template_string');
            if (firstArg) {
              const reqSource = getNodeText(firstArg).replace(/['"`]/g, '').trim();
              if (reqSource) {
                result.imports.push({
                  source: reqSource,
                  importType: callee === 'require' ? 'require' : 'dynamic_import',
                  location: loc,
                  line: loc.startLine
                });
                return;
              }
            }
          }
        }

        result.calls.push({
          callee,
          location: loc
        });

        // Routes detection (JS style: router.get('/path'), app.post('/path'))
        if (/\.(get|post|put|delete|patch|use|route)$/.test(callee)) {
          const argsNode = node.children.find(c => c.type === 'arguments' || c.type === 'argument_list');
          if (argsNode && argsNode.children.length > 1) {
            const firstArg = argsNode.children.find(c => c.type === 'string' || c.type === 'template_string');
            if (firstArg) {
              const routeMethod = callee.split('.').pop().toUpperCase();
              const routePath = getNodeText(firstArg).replace(/['"`]/g, '');
              if (!result.routes.some(r => r.path === routePath)) {
                result.routes.push({
                  method: routeMethod,
                  path: routePath,
                  location: loc
                });
              }
            }
          }
        }

        // DB Operations detection
        const dbKeywords = ['find', 'findOne', 'findAll', 'create', 'save', 'update', 'delete', 'destroy', 'query', 'execute', 'connect', 'commit', 'rollback', 'cursor'];
        if (dbKeywords.some(kw => callee.endsWith('.' + kw) || callee.includes(kw))) {
          result.databaseOperations.push({
            operation: callee,
            location: loc
          });
        }
      }
    }

    // Python Decorated definitions (e.g. @app.route('/api/transactions', methods=['POST']))
    if (type === 'decorated_definition') {
      node.children.forEach(child => {
        if (child.type === 'decorator') {
          const decText = getNodeText(child);
          if (decText && decText.includes('.route(')) {
            const pathMatch = decText.match(/\.route\(\s*['"]([^'"]+)['"]/);
            const methodMatch = decText.match(/methods\s*=\s*\[\s*['"]([A-Z]+)['"]/i);
            if (pathMatch) {
              const routeMethod = methodMatch ? methodMatch[1].toUpperCase() : 'GET';
              const routePath = pathMatch[1];
              const existingIdx = result.routes.findIndex(r => r.path === routePath);
              if (existingIdx >= 0) {
                // Update with specific HTTP method if previously generic
                result.routes[existingIdx].method = routeMethod;
              } else {
                result.routes.push({
                  method: routeMethod,
                  path: routePath,
                  location: loc
                });
              }
            }
          }
        }
      });
    }

    // JSX Elements (JS / TS)
    if (type === 'jsx_element' || type === 'jsx_self_closing_element') {
      const opening = node.children.find(c => c.type === 'jsx_opening_element' || c.type === 'jsx_self_closing_element');
      if (opening) {
        const nameNode = opening.children.find(c => c.type === 'identifier');
        if (nameNode) {
          const name = getNodeText(nameNode);
          result.react.jsxElements.push({
            name,
            location: loc
          });
          if (name[0] && name[0] === name[0].toUpperCase()) {
            if (!result.react.components.includes(name)) {
              result.react.components.push(name);
            }
          }
        }
      }
    }

    // Traverse child nodes
    for (let i = 0; i < node.childCount; i++) {
      traverse(node.child(i));
    }
  }

  try {
    traverse(parseResult.tree.rootNode);
  } catch (err) {
    result.parse.errors.push(`Extraction error: ${err.message}`);
  }

  return result;
}

module.exports = { extractInfo };
