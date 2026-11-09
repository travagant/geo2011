// Что реально уходит в SDXL (server/prompt.mjs + server/schema.mjs).
// Эти тесты фиксируют поведение базы: оно и было причиной «одна и та же формулировка — разный результат».
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildSdxlPrompt, needsTranslation, describePromptMode,
  PRODUCT_PREFIX, PRODUCT_SUFFIX, SDXL_NEGATIVE, SDXL_SAMPLER,
} from '../server/prompt.mjs';
import { sdxlWorkflow, generationSchema } from '../server/schema.mjs';

test('режим product оборачивает промпт: текст пользователя не доходит как есть', () => {
  const built = buildSdxlPrompt('рыцарь в доспехах');
  assert.equal(built.wrapped, true);
  assert.ok(built.text.startsWith(PRODUCT_PREFIX));
  assert.ok(built.text.endsWith(PRODUCT_SUFFIX));
  assert.match(built.text, /рыцарь в доспехах/);
  assert.match(built.text, /isolated on solid white background/);
});

test('режим literal отдаёт текст как написан, без обёртки', () => {
  const built = buildSdxlPrompt('  knight in worn armour, red cloak  ', { mode: 'literal' });
  assert.equal(built.text, 'knight in worn armour, red cloak');
  assert.equal(built.wrapped, false);
});

test('пустой промпт — понятная ошибка, а не пустой CLIP', () => {
  assert.throws(() => buildSdxlPrompt('   '), /Пустой промпт/);
  assert.throws(() => buildSdxlPrompt(null), /Пустой промпт/);
});

test('перевод включается любым не-ASCII символом, включая тире и ёлочки', () => {
  assert.equal(needsTranslation('рыцарь'), true);
  assert.equal(needsTranslation('knight — sword'), true, 'длинное тире уже не ASCII');
  assert.equal(needsTranslation('«knight»'), true);
  assert.equal(needsTranslation('ёлка'), true);
  assert.equal(needsTranslation('plain english prompt, 2 swords'), false);
  assert.equal(needsTranslation(''), false);
});

test('граф SDXL: literal — тот же текст в CLIP, product — обёрнутый', () => {
  const literal = sdxlWorkflow('knight', 'sd_xl_base_1.0.safetensors', 42, 'p', { mode: 'literal' });
  assert.equal(literal['2'].inputs.text, 'knight');
  assert.equal(literal['3'].inputs.text, SDXL_NEGATIVE);
  assert.equal(literal['5'].inputs.steps, SDXL_SAMPLER.steps);
  assert.equal(literal['5'].inputs.cfg, SDXL_SAMPLER.cfg);
  assert.equal(literal['4'].inputs.width, SDXL_SAMPLER.width);

  const product = sdxlWorkflow('knight', 'sd_xl_base_1.0.safetensors', 42, 'p');
  assert.equal(product['2'].inputs.text, PRODUCT_PREFIX + 'knight' + PRODUCT_SUFFIX);

  const custom = sdxlWorkflow('knight', 'sd_xl_base_1.0.safetensors', 42, 'p', { mode: 'literal', negative: 'text, blurry', sampler: { steps: 30, width: 1024, height: 1024 } });
  assert.equal(custom['3'].inputs.text, 'text, blurry');
  assert.equal(custom['5'].inputs.steps, 30);
  assert.equal(custom['4'].inputs.width, 1024);
});

test('схема задания принимает promptMode и по умолчанию даёт поведение базы', () => {
  const plain = generationSchema.parse({ prompt: 'knight' });
  assert.equal(plain.promptMode, 'product');
  const literal = generationSchema.parse({ prompt: 'рыцарь', promptMode: 'literal' });
  assert.equal(literal.promptMode, 'literal');
  assert.equal(generationSchema.safeParse({ prompt: 'knight', promptMode: 'weird' }).success, false);
});

test('подпись режима объясняет разницу по-русски', () => {
  assert.match(describePromptMode('literal'), /как написано/);
  assert.match(describePromptMode('product'), /перевод на английский/);
  assert.match(describePromptMode(undefined), /перевод на английский/);
});
