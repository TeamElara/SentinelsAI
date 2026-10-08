import { readFileSync } from 'node:fs';
import ts from 'typescript';

export async function loadTs(file) {
  const source = readFileSync(new URL(file, import.meta.url), 'utf8');
  let code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
  if (code.includes("'./scanner-health'")) {
    const health = readFileSync(new URL('../lib/scanner-health.ts', import.meta.url), 'utf8');
    const compiled = ts.transpileModule(health, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
    code = code.replace("'./scanner-health'", "'data:text/javascript;base64," + Buffer.from(compiled).toString('base64') + "'");
  }
  return import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
}
