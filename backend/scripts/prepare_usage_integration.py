"""Prepare a reviewable route patch AFTER A1/A2/A4 are merged.

This tool writes a patch, never modifies main.py. The ownership helper and
cross-site guard are required. Run the route-ownership suite before applying.
"""
import argparse
import ast
import copy
import difflib
from pathlib import Path


def transform(source):
    tree = ast.parse(source)
    functions = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for prerequisite in ('load_owned_scan', 'reject_cross_site_scan_start'):
        if prerequisite not in functions:
            raise ValueError(f'A1/A2 prerequisite missing: {prerequisite}')
    replacements = {}
    for name, kind, runner in [('scan','url_scan','run_scan'),('repo_scan','repo_scan','run_repo_scan')]:
        args = ast.unparse(functions[name].args)
        permission = ''.join('    ' + ast.unparse(statement) + '\n' for statement in functions[name].body
                             if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
                             and isinstance(statement.value.func, ast.Name) and statement.value.func.id == 'require_permission')
        replacements[name] = f'''async def {name}({args}) -> ScanReport:
{permission}\
    try:
        return await scan_operation(user.id, '{kind}', lambda: {runner}(request.url, user_id=user.id))
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
'''
    for name, kind, runner in [('scan_stream','url_scan','run_scan_stream'),('repo_scan_stream','repo_scan','run_repo_scan_stream')]:
        arguments = copy.deepcopy(functions[name].args)
        if 'request' not in {arg.arg for arg in arguments.args}:
            raise ValueError(f'A1 request guard missing in {name}')
        if 'request_id' in {arg.arg for arg in arguments.args}:
            raise ValueError('C7 is already integrated; do not apply twice')
        arguments.args.insert(2, ast.arg(arg='request_id', annotation=ast.Name(id='str', ctx=ast.Load())))
        args = ast.unparse(arguments)
        permission = ''.join('    ' + ast.unparse(statement) + '\n' for statement in functions[name].body
                             if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
                             and isinstance(statement.value.func, ast.Name) and statement.value.func.id == 'require_permission')
        replacements[name] = f'''async def {name}({args}) -> StreamingResponse:
    reject_cross_site_scan_start(request)
{permission}\
    async def events():
        async for event, payload in {runner}(url, user_id=user.id):
            yield event, payload.model_dump_json()
    return await stream_response(user.id, '{kind}', url, request_id, events)
'''
    replacements['finding_fix'] = '''async def finding_fix(scan_id: str, finding_key: str, regenerate: bool = False, user: User = Depends(current_user)) -> FixSuggestion:
    report = load_owned_scan(scan_id, user)
    finding = next((f for f in report.findings if f.id == finding_key), None)
    if finding is None:
        raise HTTPException(404, 'Finding not found')
    suggestion = await get_or_generate_fix(scan_id, finding_key, finding, regenerate=regenerate, user_id=user.id)
    if suggestion is None:
        raise HTTPException(503, 'AI fix generation is unavailable. Cached suggestions remain usable.')
    return suggestion
'''
    replacements['finding_verify'] = '''async def finding_verify(scan_id: str, finding_key: str, user: User = Depends(current_user)) -> VerificationResult:
    report = load_owned_scan(scan_id, user)
    async def verify():
        try:
            return await verify_finding(report, user, finding_key)
        except VerifyError as exc:
            raise HTTPException(exc.status, detail=str(exc)) from exc
    return await charged(user.id, 'verify', verify)
'''
    replacements['chat_post'] = '''async def chat_post(scan_id: str, body: ChatQuestion, user: User = Depends(current_user)) -> ChatMessage:
    report = load_owned_scan(scan_id, user)
    question = body.question.strip()
    if not question or len(question) > 12000:
        raise HTTPException(422, 'Question must contain 1–12000 characters.')
    message = await chat_answer(scan_id, report, report.checklist, question, user_id=user.id)
    if message is None:
        raise HTTPException(503, 'Chat is unavailable. The report is still usable.')
    return message
'''
    replacements['scan_pdf'] = '''async def scan_pdf(report: ScanReport, user: User = Depends(current_user)) -> Response:
    # Compatibility body supplies only an ID; all printable data is reloaded.
    return await scan_export(report.id, 'pdf', user)
'''
    if 'scan_pdf' not in functions:
        del replacements['scan_pdf']  # A4 removes this legacy endpoint; never restore it.
    lines = source.splitlines(keepends=True)
    for name, text in sorted(replacements.items(), key=lambda item: functions[item[0]].lineno, reverse=True):
        node = functions[name]
        lines[node.lineno-1:node.end_lineno] = [text]
    result = ''.join(lines)
    result = result.replace('    content = await exporter.render(report, fixes)', "    content = await pdf_operation(user.id, lambda: exporter.render(report, fixes)) if format_id == 'pdf' else await exporter.render(report, fixes)")
    result = result.replace('from rate_limit import enforce_scan_rate_limit', 'from budgeted_operations import charged, scan_operation, pdf_operation, stream_response, router as usage_router\nfrom rate_limit import enforce_scan_rate_limit')
    result += '\n# Mount only after ownership prerequisites and their route-walking tests pass.\napp.include_router(usage_router)\n'
    compile(result, 'main.py', 'exec')
    return result


def transform_permission_ui(source):
    if 'const [permission, setPermission]' not in source or 'needsPermission' not in source:
        raise ValueError('A6 permission checkbox has not landed; never invent confirmation')
    ending = '    });\n  }\n\n  return ('
    if source.count(ending) != 1 or 'stream(url, {' not in source:
        raise ValueError('Review the launcher stream callback before integrating permission')
    return source.replace(ending, '    }, permission);\n  }\n\n  return (')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1] / 'main.py')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--frontend-source', type=Path)
    parser.add_argument('--frontend-output', type=Path)
    args = parser.parse_args()
    original = args.source.read_text(encoding='utf-8')
    updated = transform(original)
    args.output.write_text(''.join(difflib.unified_diff(original.splitlines(keepends=True), updated.splitlines(keepends=True), fromfile='a/backend/main.py', tofile='b/backend/main.py')), encoding='utf-8')
    if args.frontend_source:
        if not args.frontend_output:
            parser.error('--frontend-output is required with --frontend-source')
        original = args.frontend_source.read_text(encoding='utf-8')
        updated = transform_permission_ui(original)
        args.frontend_output.write_text(''.join(difflib.unified_diff(original.splitlines(keepends=True), updated.splitlines(keepends=True), fromfile='a/frontend/components/ScanLauncher.tsx', tofile='b/frontend/components/ScanLauncher.tsx')), encoding='utf-8')
