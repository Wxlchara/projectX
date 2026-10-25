import assert from 'node:assert/strict';

const callbacks = new Map();
const app = { innerHTML: '' };
const toast = { textContent: '', className: '', classList: { remove() {} } };
const filters = {
  'continent-filter': { value: 'all' },
  'schengen-filter': { value: 'all' },
  'entry-filter': { value: 'all' }
};

globalThis.location = { hash: '#/' };
globalThis.window = {
  addEventListener(type, callback) { callbacks.set(type, callback); },
  scrollTo() {},
  scrollY: 0
};
globalThis.document = {
  title: '',
  addEventListener(type, callback) { callbacks.set(type, callback); },
  getElementById(id) { return id === 'app' ? app : id === 'toast' ? toast : filters[id] || null; }
};
globalThis.fetch = async () => { throw new Error('No backend in smoke test'); };

await import('../app.js');
await new Promise(resolve => setTimeout(resolve, 0));

function assertBalancedMarkup(html, route) {
  const stack = [];
  const voidTags = new Set(['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']);
  for (const match of html.matchAll(/<\/?([a-z][\w:-]*)\b[^>]*>/gi)) {
    const tag = match[1].toLowerCase();
    if (voidTags.has(tag)) continue;
    if (match[0].startsWith('</')) assert.equal(stack.pop(), tag, `${route}: unbalanced </${tag}>`);
    else stack.push(tag);
  }
  assert.deepEqual(stack, [], `${route}: unclosed elements`);
}

function assertInternalLinks(html, route) {
  const valid = /^\/(?:$|about$|auth$|destinations(?:\/[^/]+)?$|compare$|profile$|cases(?:\/new|\/[^/]+(?:\/(?:edit|checklist|route|letter|timeline))?)?$|archive(?:\/[^/]+)?$)/;
  for (const match of html.matchAll(/href="#(\/[^"?]*)(?:\?[^\"]*)?"/g)) {
    assert.match(match[1], valid, `${route}: unknown internal route ${match[1]}`);
  }
}

async function visit(hash, text) {
  location.hash = hash;
  await callbacks.get('hashchange')();
  assert.ok(app.innerHTML.includes(text), `${hash} should contain ${text}`);
  assert.ok(app.innerHTML.includes('Visa<span>Mate</span>'), 'brand should be VisaMate');
  assert.ok(!app.innerHTML.includes('VisaPilot'), 'old brand must not appear');
  assertBalancedMarkup(app.innerHTML, hash);
  assertInternalLinks(app.innerHTML, hash);
}

assert.ok(app.innerHTML.includes('Шенген: Любой'), 'search should be on the home screen');
assert.ok(app.innerHTML.includes('country-icon'), 'country card icons should be present');
assert.ok(!app.innerHTML.includes('АКТИВНОЕ ДЕЛО'), 'home should not duplicate a single case');
assert.equal((app.innerHTML.match(/class="country-card"/g) || []).length, 6, 'home should show a short country preview');
assert.ok(app.innerHTML.includes('Все результаты'), 'home preview should link to the full directory');
assertBalancedMarkup(app.innerHTML, '#/');
assertInternalLinks(app.innerHTML, '#/');
await visit('#/destinations', 'Поиск по странам');
filters['schengen-filter'].value = 'yes';
callbacks.get('change')({ target: { id: 'schengen-filter' } });
assert.ok(app.innerHTML.includes('2 результатов'), 'Schengen filter should show France and Spain');
assert.ok(!app.innerHTML.includes('<h3>Япония</h3>'), 'Schengen filter should exclude Japan');
filters['schengen-filter'].value = 'all';
callbacks.get('change')({ target: { id: 'schengen-filter' } });
await visit('#/destinations/FR_TOURISM', 'Франция');
await visit('#/compare', 'Сравнение');
const OriginalFormData = globalThis.FormData;
globalThis.FormData = class {
  constructor(form) { this.fields = form.fields; }
  *[Symbol.iterator]() { yield* Object.entries(this.fields); }
};
const compareButton = { disabled: false };
const compareForm = {
  dataset: { form: 'compare' },
  fields: { starts_on:'2027-06-10', ends_on:'2027-06-20', departure_city:'Москва', application_country:'RU' },
  querySelector() { return compareButton; }
};
await callbacks.get('submit')({ target: { closest() { return compareForm; } }, preventDefault() {} });
globalThis.FormData = OriginalFormData;
assert.ok(app.innerHTML.includes('id="comparison-country"'), 'comparison should offer a country dropdown');
assert.equal((app.innerHTML.match(/class="comparison-row surface-card"/g) || []).length, 0, 'comparison should not list all countries');
callbacks.get('change')({ target: { id:'comparison-country', value:'FR_TOURISM' } });
assert.equal((app.innerHTML.match(/class="comparison-row surface-card"/g) || []).length, 1, 'only selected country should appear');
await visit('#/profile', 'Профиль');
assert.ok(!app.innerHTML.includes('name="citizenship"'), 'fixed Russian citizenship should not be a form field');
assert.ok(!app.innerHTML.includes('class="workspace-side"'), 'workspace should not duplicate the header menu');
await visit('#/cases', 'Мои дела');
await visit('#/cases/new?scenario=JP_TOURISM', 'Новое дело');
await visit('#/cases/demo-france', 'Готовность пакета');
await visit('#/cases/demo-france/edit', 'Изменить поездку');
await visit('#/cases/demo-france/checklist', 'Smart Checklist');
await visit('#/archive', 'Личный архив');
await visit('#/archive/sample-passport', 'Поля документа');
await visit('#/cases/demo-france/route', 'Пересадка');
await visit('#/cases/demo-france/letter', 'Черновик');
await visit('#/cases/demo-france/timeline', 'Хронология');
await visit('#/about', 'Что делает VisaMate');

console.log('VisaMate: 15 routes and the Schengen filter passed in demo mode.');
