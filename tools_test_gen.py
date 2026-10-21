import re, pathlib, json, urllib.request

s = pathlib.Path('frontend/report.html').read_text(encoding='utf-8')
md = urllib.request.urlopen(
    'http://127.0.0.1:8000/reports/report_20261006_200631_comparative_analysis_of_solar_photovolta.md'
).read().decode('utf-8')
fm = re.match(r'^---\r?\n([\s\S]*?)\r?\n---\r?\n?', md)
body = md[len(fm[0]):]

def grab(name, endname):
    i = s.find('function ' + name + '(')
    if endname.startswith('const'):
        j = s.find(endname + ' = {')
    else:
        j = s.find('function ' + endname + '(')
    assert i != -1 and j != -1 and j > i, (name, endname, i, j)
    return s[i:j]

parts = 'let questBody = ' + json.dumps(body) + ';\n'
parts += 'const reduced=false;\n'
parts += 'const reportStubEl = { textContent: "Report" };\n'
parts += 'const $ = () => reportStubEl;\n'
parts += 'function escHtml(s){ return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;"); }\n'
parts += 'function mdInline(s){ return s; }\n'
parts += grab('qSections', 'qSents') + grab('qSents', 'qParas') + grab('qParas', 'qTitle')
parts += grab('qTitle', 'qBuild') + grab('qBuild', 'const OUT_META')
parts += '''
console.log('sections:', qSections().length);
for (const id of ['quiz', 'slides', 'flash', 'timeline', 'mindmap', 'audio', 'table', 'data']) {
  try {
    const b = qBuild(id);
    console.log(id, b ? ('OK   raw=' + b.raw.length + '  html=' + b.html.length) : 'NULL');
  } catch (e) { console.log(id, 'THREW', e.message); }
}
'''
p = pathlib.Path('frontend/__t.js')
p.write_text(parts, encoding='utf-8')
print('harness written')
