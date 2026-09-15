const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2] || require('node:path').join(__dirname, '../doctor-notes.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.append(node); }
  replaceChildren() { this.children = []; this.textContent = ''; }
}
const container = new Element('div');
const context = vm.createContext({document: {createElement: tag => new Element(tag)}, $: () => container});
vm.runInContext(script.slice(script.indexOf('    function renderCleanContent'), script.indexOf('    function showEdit')), context);
function render(content) { context.renderCleanContent(content); return container.children[0]; }
let list = render('- **Jardiance**\n  - Sodium/Chloride/Electrolytes\n  - Ketone Meter\n  - Insulin Doses\n- **Mounjaro Improvements**\n  - Weight\n  - Appetite\n  - Insulin');
assert.equal(list.children.length, 2);
assert.equal(list.children[0].children[0].children[1].children.length, 3);
assert.equal(list.children[1].children[0].children[0].textContent, 'Mounjaro Improvements');
list = render('- Parent\n\n\t* Child\n\t  • Grandchild\n\t* Next child\n- Next topic');
assert.equal(list.children.length, 2);
assert.equal(list.children[0].children[0].children[1].children[0].children[0].children[0].textContent, 'Grandchild');
list = render('- Topic: original\n  continuation\n- Second: unchanged\n\nClosing paragraph');
assert.equal(list.children[0].children[0].children[1].textContent, 'original continuation');
assert.equal(container.children[1].textContent, 'Closing paragraph');
list = render('- Parent\n  - <img src=x onerror=alert(1)>');
assert.equal(list.children[0].children[0].children[1].children[0].textContent, '<img src=x onerror=alert(1)>');
render(''); assert.equal(container.textContent, 'No note text.');
console.log('PASS: syntax, nested topics, three levels, tabs, blank lines, continuation, closing text, literal HTML, empty notes');
