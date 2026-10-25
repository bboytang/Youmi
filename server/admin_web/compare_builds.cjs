// Compare the deployed desktop build with the reconstructed source build.
// Ignore compiler scope hashes, chunk/module IDs and local variable mangling;
// retain property names, literals, control flow, imports and CSS declarations.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { createRequire } = require('node:module');
const dependency = createRequire(path.resolve(process.env.YOUMI_ANALYSIS_PACKAGE || process.env.MIA_ANALYSIS_PACKAGE || '/app/package.json'));
const parser = dependency('@babel/parser');
const traverse = dependency('@babel/traverse').default;
const postcss = dependency('postcss');
const hash = value => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
const scopeHash = value => value.replace(/data-v-[a-f0-9]{8}/g, 'data-v-SCOPE');
const files = (root, dir, suffix) => fs.readdirSync(path.join(root, dir)).filter(name => name.endsWith(suffix)).map(name => path.join(root, dir, name));

function clean(node) {
  if (Array.isArray(node)) return node.map(clean);
  if (!node || typeof node !== 'object') return node;
  const result = {};
  for (const key of Object.keys(node).sort()) {
    if (['start','end','loc','extra','leadingComments','trailingComments','innerComments','comments'].includes(key)) continue;
    result[key] = clean(node[key]);
  }
  return result;
}

function moduleFingerprint(node) {
  const copy = JSON.parse(JSON.stringify(node));
  const ast = { type: 'File', program: { type: 'Program', sourceType: 'script', directives: [], body: [{ type: 'ExpressionStatement', expression: copy }] } };
  const imports = [];
  let loader = null;
  let nextBinding = 0;
  const labels = new Map();
  const bindings = new Map();
  const rename = new Map();
  traverse(ast, {
    Scope(p) {
      for (const binding of Object.values(p.scope.bindings).sort((a, b) => a.identifier.start - b.identifier.start)) {
        const name = '__local_' + nextBinding++;
        bindings.set(binding, name);
        if (p.node === copy && copy.params[2] === binding.identifier) loader = name;
      }
    },
    LabeledStatement(p) { labels.set(p.node, '__label_' + labels.size); },
  });
  traverse(ast, {
    Identifier(p) {
      if (p.key === 'label') {
        const parent = p.parentPath.isLabeledStatement() ? p.parentPath : p.findParent(x => x.isLabeledStatement() && x.node.label.name === p.node.name);
        if (parent && labels.has(parent.node)) rename.set(p.node, labels.get(parent.node));
        return;
      }
      const binding = p.scope.getBinding(p.node.name);
      if (binding && (p.isReferencedIdentifier() || p.isBindingIdentifier() || (p.parentPath.isAssignmentExpression() && p.key === 'left'))) {
        rename.set(p.node, bindings.get(binding));
      }
    },
  });
  for (const [node, name] of rename) node.name = name;
  traverse(ast, {
    CallExpression(p) {
      const call = p.node;
      const callee = call.callee;
      let index = -1;
      let kind = '';
      if (callee.type === 'Identifier' && callee.name === loader) { index = 0; kind = 'require'; }
      else if (callee.type === 'MemberExpression' && callee.object.type === 'Identifier' && callee.object.name === loader && !callee.computed) {
        if (['e','t'].includes(callee.property.name)) { index = 0; kind = callee.property.name; }
        if (callee.property.name === 'bind') { index = 1; kind = 'require'; }
      }
      if (index >= 0 && call.arguments[index]?.type === 'NumericLiteral') {
        if (kind !== 'e') imports.push(call.arguments[index].value);
        call.arguments[index].value = 0;
      }
      // Vue 2 component normalizer: options, render, static renders, functional,
      // injection, scoped ID, SSR ID. Preserve whether a scoped style exists.
      if (call.arguments.length === 7 && call.arguments[2]?.type === 'ArrayExpression' && call.arguments[5]?.type === 'StringLiteral' && /^[a-f0-9]{8}$/.test(call.arguments[5].value)) {
        call.arguments[5].value = 'SCOPE';
      }
    },
    ObjectProperty(p) { if (p.node.shorthand) p.node.shorthand = false; },
  });
  const names = new Set();
  traverse(ast, { ObjectProperty(p) {
    if ((p.node.key.name === 'name' || p.node.key.value === 'name') && p.node.value.type === 'StringLiteral') names.add(p.node.value.value);
  }});
  return { hash: hash(clean(copy)), imports, names: [...names], ...((process.env.YOUMI_DEBUG_AST || process.env.MIA_DEBUG_AST) ? { ast: clean(copy) } : {}) };
}

function snapshot(root) {
  const css = new Set();
  for (const file of files(root, 'css', '.css')) {
    postcss.parse(fs.readFileSync(file, 'utf8')).walkRules(rule => {
      const parents = [];
      for (let p = rule.parent; p; p = p.parent) if (p.type === 'atrule') parents.unshift([p.name, scopeHash(p.params)]);
      const declarations = rule.nodes.filter(node => node.type === 'decl').map(node => [node.prop, scopeHash(node.value.trim().replace(/\s+/g, ' ')), !!node.important]);
      css.add(hash([parents, scopeHash(rule.selector.trim().replace(/\s+/g, ' ')), declarations]));
    });
  }
  const modules = new Map();
  for (const file of files(root, 'js', '.js')) {
    const ast = parser.parse(fs.readFileSync(file, 'utf8'), { sourceType: 'unambiguous' });
    traverse(ast, { ObjectExpression(p) {
      const properties = p.node.properties;
      if (!properties.length || !properties.every(prop => prop.type === 'ObjectProperty' && /^\d+$/.test(String(prop.key.value)) && ['FunctionExpression','ArrowFunctionExpression'].includes(prop.value.type))) return;
      for (const property of properties) {
        const id = String(property.key.value);
        if (!modules.has(id)) modules.set(id, moduleFingerprint(property.value));
      }
      p.skip();
    }});
  }
  return { css: [...css].sort(), modules: Object.fromEntries(modules) };
}

module.exports = { moduleFingerprint, snapshot };
if (require.main === module) {
const [mode, input, output] = process.argv.slice(2);
if (mode === '--snapshot') {
  fs.writeFileSync(output, JSON.stringify(snapshot(input), null, 2) + '\n');
} else if (mode === '--compare') {
  const expected = JSON.parse(fs.readFileSync(input, 'utf8'));
  const actual = snapshot(output);
  const actualHashes = new Map();
  for (const [id, value] of Object.entries(actual.modules)) {
    if (!actualHashes.has(value.hash)) actualHashes.set(value.hash, []);
    actualHashes.get(value.hash).push(id);
  }
  const missing = Object.entries(expected.modules).filter(([, value]) => !actualHashes.has(value.hash));
  const missingCss = expected.css.filter(value => !actual.css.includes(value));
  const extraCss = actual.css.filter(value => !expected.css.includes(value));
  // Once modules match, verify that each import points to the same module body.
  const brokenImports = Object.entries(expected.modules).filter(([, value]) => {
    const candidates = actualHashes.get(value.hash);
    if (!candidates) return false;
    // Several generated require wrappers can share a body while importing
    // different modules. Match the body together with its ordered imports.
    return !candidates.some(candidate => {
      const target = actual.modules[candidate];
      return target.imports.length === value.imports.length && value.imports.every((id, index) =>
        expected.modules[id]?.hash === actual.modules[target.imports[index]]?.hash);
    });
  });
  const report = {
    expectedModules: Object.keys(expected.modules).length,
    matchedModules: Object.keys(expected.modules).length - missing.length,
    missing: missing.map(([id, value]) => ({ id, names: value.names })),
    brokenImports: brokenImports.map(([id, value]) => ({ id, names: value.names })),
    expectedCssRules: expected.css.length, matchedCssRules: expected.css.length - missingCss.length,
    missingCss, extraCss,
  };
  console.log(JSON.stringify(report, null, 2));
  if (missing.length || missingCss.length || extraCss.length || brokenImports.length) process.exitCode = 1;
} else {
  throw new Error('Use --snapshot <production-dir> <output.json> or --compare <snapshot.json> <baseline-dir>');
}

}
