#!/usr/bin/env python3
"""Read-only Skill file inventory. JSON schema v2; exit 0=inventory, 2=input/environment error."""

import argparse
import json
import os
from pathlib import Path
import posixpath
import re
import sys
from urllib.parse import unquote

try:
    import yaml
except ImportError:
    yaml = None

RESOURCE_DIRS = ('references', 'assets', 'scripts', 'templates', 'examples', 'tests', 'agents')
IGNORED_DIRS = {'.git', '.venv', '__pycache__'}


def fail(message):
    print(json.dumps({'error': message}, ensure_ascii=False))
    raise SystemExit(2)


def parse_frontmatter(text):
    """Return YAML mapping, rejecting absent, malformed or non-mapping frontmatter."""
    match = re.match(r'\A---[ \t]*\n(.*?)^---[ \t]*(?:\n|\Z)', text.lstrip('\ufeff'), re.S | re.M)
    if not match:
        raise ValueError('缺少或未闭合的 YAML frontmatter')
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ValueError('YAML frontmatter 无效: %s' % exc) from exc
    if not isinstance(data, dict):
        raise ValueError('YAML frontmatter 必须为键值映射')
    return data


def outside_reference(route):
    return route.startswith(('/', '\\')) or bool(re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', route))


def normalize_route(route):
    route = route.strip().strip('<>')
    if not route or route.startswith('#'):
        return None
    if outside_reference(route):
        return route
    route = route.replace('\\(', '(').replace('\\)', ')').replace('\\', '/')
    route = unquote(re.split(r'[?#]', route, maxsplit=1)[0])
    return posixpath.normpath(route) if route else None


def without_fences(text):
    lines = []
    marker = None
    length = 0
    for line in text.splitlines():
        match = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
        if match:
            fence = match.group(1)
            if marker is None:
                marker, length = fence[0], len(fence)
            elif fence[0] == marker and len(fence) >= length and not match.group(2).strip():
                marker = None
            continue
        if marker is None:
            lines.append(line)
    return '\n'.join(lines)


def extract_routes(text):
    """Common inline code, Markdown links and reference definitions; not a full Markdown parser."""
    text = without_fences(text)
    candidates = []
    for match in re.finditer(r'`([^`\n]+)`', text):
        token = match.group(1)
        if match.start() and text[match.start() - 1] == '!':
            candidates.append('!`' + token + '`')
            continue
        if re.match(r'^(?:python[\d.]*|python\.exe|node|bash|sh|pwsh|powershell|uv|pip|npm|npx)\s', token):
            continue  # Commands are not paths; their arguments need manual review.
        token = re.split(r'\s+(?:<|--)', token.strip(), maxsplit=1)[0]
        normalized = normalize_route(token)
        if normalized and (outside_reference(normalized)
                           or normalized.split('/')[0] in RESOURCE_DIRS
                           or re.search(r'\.[A-Za-z0-9]{1,10}$', normalized)):
            candidates.append(normalized)
    # shortcut: common Markdown destinations only; use a Markdown parser if nested syntax is required.
    destination = r'(<[^>\n]+>|(?:\\.|[^\s()])+)(?:\s+[\"\'][^\n]*?[\"\'])?'
    for match in re.finditer(r'\]\(\s*' + destination + r'\s*\)', text):
        candidates.append(normalize_route(match.group(1)))
    for match in re.finditer(r'^\s{0,3}\[[^\]\n]+\]:\s*' + destination, text, re.M):
        candidates.append(normalize_route(match.group(1)))
    return sorted({route for route in candidates if route and route != '.'})


def list_dir_files(base, sub):
    directory = base / sub
    if not directory.is_dir():
        return None
    files = []
    if not directory.is_symlink():
        for root, dirs, names in os.walk(directory):
            dirs[:] = [name for name in dirs if name not in IGNORED_DIRS
                       and not (Path(root) / name).is_symlink()]
            files.extend((Path(root) / name).relative_to(base).as_posix() for name in names)
    return sorted(files)


def inspect_references(base, entry, text, recursive, path_base):
    pending = [(entry, text)]
    seen = {entry.resolve()}
    scanned, missing, linked, external, warnings = [], set(), set(), [], []
    deferred, ambiguous = [], []
    while pending:
        source, body = pending.pop(0)
        source_name = source.relative_to(base).as_posix()
        scanned.append(source_name)
        for route in extract_routes(body):
            if outside_reference(route):
                external.append({'source': source_name, 'path': route})
                continue
            if route.startswith('!`') or re.search(r'\$\{|\$\(|\$[A-Za-z_]|\{\{|%[A-Za-z_][A-Za-z0-9_]*%|[<>*]', route):
                deferred.append({'source': source_name, 'path': route})
                continue
            roots = {'document': [source.parent], 'skill': [base],
                     'auto': [source.parent, base]}[path_base]
            targets = list(dict.fromkeys((root / route).resolve() for root in roots))
            local = [target for target in targets if target.is_relative_to(base)]
            if not local:
                external.append({'source': source_name, 'path': route})
                continue
            existing = [target for target in local if target.exists()]
            linked.update(target.relative_to(base).as_posix() for target in existing)
            if not existing:
                missing.add(local[0].relative_to(base).as_posix())
                continue
            if len(existing) > 1:
                ambiguous.append({'source': source_name, 'path': route,
                                  'candidates': [target.relative_to(base).as_posix() for target in existing]})
                continue
            target = existing[0]
            name = target.relative_to(base).as_posix()
            if recursive and target.is_file() and target.suffix.lower() == '.md' and target not in seen:
                seen.add(target)
                try:
                    pending.append((target, target.read_text(encoding='utf-8-sig')))
                except (OSError, UnicodeError) as exc:
                    warnings.append('无法读取引用文档 %s: %s' % (name, exc))
    return sorted(scanned), sorted(missing), linked, external, warnings, deferred, ambiguous


def snapshot(target, path_base='auto'):
    target = Path(target).resolve()
    if target.is_file():
        if target.name.lower() != 'skill.md':
            raise ValueError('输入文件不是 SKILL.md')
        base, entry, input_type = target.parent, target, 'single_file'
    elif target.is_dir():
        base, entry, input_type = target, target / 'SKILL.md', 'directory'
    else:
        raise ValueError('路径不存在: %s' % target)

    result = {
        'schema_version': 2,
        'path_base': path_base,
        'input_path': target.as_posix(),
        'input_type': input_type,
        'skill_md': {'path': entry.name, 'exists': entry.is_file()},
        'directories': {},
        'scanned_documents': [],
        'ghost_references': [],
        'unlinked_files': [],
        'external_references': [],
        'deferred_references': [],
        'ambiguous_references': [],
        'warnings': [],
    }
    resource_files = []
    if input_type == 'directory':
        for sub in RESOURCE_DIRS:
            files = list_dir_files(base, sub)
            result['directories'][sub] = {'exists': files is not None, 'files': files or []}
            resource_files.extend(files or [])
    if not entry.is_file():
        result['warnings'].append('目录中未找到 SKILL.md；尚不能审查入口内容')
        return result
    if not entry.resolve().is_relative_to(base):
        result['warnings'].append('SKILL.md 指向根目录之外；未读取入口内容')
        return result

    text = entry.read_text(encoding='utf-8-sig')
    result['skill_md']['lines'] = len(text.splitlines())
    result['skill_md']['frontmatter'] = None
    try:
        frontmatter = parse_frontmatter(text)
    except ValueError as exc:
        result['skill_md']['frontmatter_error'] = str(exc)
        result['warnings'].append(str(exc))
    else:
        result['skill_md']['frontmatter_keys'] = sorted(str(key) for key in frontmatter)
        metadata = {}
        for key in ('name', 'description'):
            value = frontmatter.get(key)
            metadata[key] = value if isinstance(value, str) else None
            if key not in frontmatter:
                result['warnings'].append('frontmatter 未提供 %s；是否必需取决于目标宿主' % key)
            elif not isinstance(value, str) or not value.strip():
                result['warnings'].append('frontmatter 的 %s 不是非空字符串；按目标宿主要求复核' % key)
        result['skill_md']['frontmatter'] = metadata

    scanned, missing, linked, external, warnings, deferred, ambiguous = inspect_references(
        base, entry, text, input_type == 'directory', path_base)
    result['scanned_documents'] = scanned
    result['ghost_references'] = missing
    result['external_references'] = external
    result['deferred_references'] = deferred
    result['ambiguous_references'] = ambiguous
    result['warnings'].extend(warnings)
    result['unlinked_files'] = [name for name in resource_files
                               if not any(name == route or name.startswith(route + '/') for route in linked)]
    if input_type == 'single_file':
        result['warnings'].append('单文件范围：仅核对入口引用路径，未盘点或递归读取其他资源')
    return result


def main():
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            fail(message)

    parser = Parser(description=__doc__)
    parser.add_argument('target')
    parser.add_argument('--pretty', action='store_true')
    parser.add_argument('--path-base', choices=('auto', 'skill', 'document'), default='auto')
    args = parser.parse_args()
    if yaml is None:
        fail('缺少 PyYAML；请先用当前 Python 运行 -m pip install -r requirements.txt')
    try:
        result = snapshot(args.target, args.path_base)
    except (OSError, UnicodeError, ValueError) as exc:
        fail(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))


if __name__ == '__main__':
    main()
