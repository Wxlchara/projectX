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

async function visit(hash, text) {
  location.hash = hash;
  await callbacks.get('hashchange')();
  assert.ok(app.innerHTML.includes(text), `${hash} should contain ${text}`);
  assert.ok(app.innerHTML.includes('Visa<span>Mate</span>'), 'brand should be VisaMate');
  assert.ok(!app.innerHTML.includes('VisaPilot'), 'old brand must not appear');
}

assert.ok(app.innerHTML.includes('Шенген: Любой'), 'search should be on the home screen');
assert.ok(app.innerHTML.includes('country-icon'), 'country card icons should be present');
await visit('#/destinations', 'Поиск по странам');
filters['schengen-filter'].value = 'yes';
callbacks.get('change')({ target: { id: 'schengen-filter' } });
assert.ok(app.innerHTML.includes('2 результатов'), 'Schengen filter should show France and Spain');
assert.ok(!app.innerHTML.includes('<h3>Япония</h3>'), 'Schengen filter should exclude Japan');
filters['schengen-filter'].value = 'all';
callbacks.get('change')({ target: { id: 'schengen-filter' } });
await visit('#/destinations/FR_TOURISM', 'Франция');
await visit('#/compare', 'Сравнение');
await visit('#/profile', 'Профиль');
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
