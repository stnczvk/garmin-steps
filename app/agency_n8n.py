"""Podpięcie Agencji do workflow „generator” w n8n (czysta transformacja JSON, bez sieci).

Dodaje 4 node'y:
- „Agencja: temat”     (Code)    Merge Context → [tu] → Message a model   – temat z kolejki Trend Scouta
- „Agencja: kontrola”  (Code)    Judge Gate → [tu] → Script Passed?        – Kontroler jakości poprawia scenariusz
- „Agencja: start”     (Webhook) + „Agencja: token OK?” (If) → te same node'y co Schedule Trigger 8:00
Wszystkie node'y agencji mają try/catch – gdy centrum nie odpowiada, generator działa po staremu.
"""
import copy, uuid

TAG = "Agencja: "

TOPIC_JS = r"""// Agencja AI – Trend Scout podaje temat z kolejki (gdy centrum nie odpowiada: generator działa po staremu)
const base = $json;
let brief = '';
try {
  const r = await this.helpers.httpRequest({
    method: 'GET', url: '__URL__/api/agency/hook/next-topic',
    headers: { 'X-Agency-Token': '__TOKEN__' }, json: true, timeout: 150000,
  });
  if (r && r.ok && r.topic) {
    brief = '## Agency brief (MANDATORY topic for this video)\n' +
      'The agency Trend Scout picked today\'s topic. Skip step 1 (TOPICS) and build the video around it ' +
      '(sharpen the angle if needed, keep the subject) unless it is a near-duplicate of a recent video.\n' +
      '- topic: ' + r.topic + '\n- pillar: ' + (r.pillar || r.category || '') + '\n- hook idea: ' + (r.hook || '') +
      '\n- familiar reference: ' + (r.reference || '') + '\n- payoff: ' + (r.payoff || '') + '\n';
  }
} catch (e) {}
return [{ json: { ...base, agencyTopic: !!brief, insightsText: (brief ? brief + '\n' : '') + (base.insightsText || '') } }];"""

REVIEW_JS = r"""// Agencja AI – Kontroler jakości (Claude) ocenia i w razie potrzeby poprawia scenariusz
const g = $json;
try {
  const r = await this.helpers.httpRequest({
    method: 'POST', url: '__URL__/api/agency/hook/review',
    headers: { 'X-Agency-Token': '__TOKEN__' }, json: true, timeout: 170000,
    body: { script: g.output[0].content[0].text, judge: { score: g.judgeScore, pass: g.pass, problems: g.problems } },
  });
  if (r && r.script && typeof r.script === 'object') {
    return [{ json: { ...g, output: [{ content: [{ text: r.script }] }],
      pass: r.pass === true ? true : g.pass, agencyScore: r.score, agencyRewritten: !!r.rewritten,
      feedback: r.pass === true ? '' : g.feedback } }];
  }
} catch (e) {}
return [{ json: g }];"""


def _code(name, js, pos):
    return {"parameters": {"jsCode": js}, "id": str(uuid.uuid4()), "name": name, "type": "n8n-nodes-base.code",
            "typeVersion": 2, "position": pos}


def find_generator(workflows):
    c = [w for w in workflows if {"Merge Context", "Message a model", "Judge Gate", "Script Passed?"} <= {n["name"] for n in w.get("nodes", [])}]
    c.sort(key=lambda w: (not w.get("active"), w.get("name") != "generator"))
    return c[0] if c else None


def is_connected(wf):
    return any(n["name"].startswith(TAG) for n in wf.get("nodes", []))


def strip(wf):
    """Usuwa node'y agencji i przywraca oryginalne połączenia."""
    wf = copy.deepcopy(wf)
    names = {n["name"] for n in wf["nodes"] if n["name"].startswith(TAG)}
    wf["nodes"] = [n for n in wf["nodes"] if n["name"] not in names]
    C = wf["connections"]
    for k in names:
        C.pop(k, None)
    C["Merge Context"] = {"main": [[{"node": "Message a model", "type": "main", "index": 0}]]}
    C["Judge Gate"] = {"main": [[{"node": "Script Passed?", "type": "main", "index": 0}]]}
    return wf


def patch(wf, url, token):
    wf = strip(wf) if is_connected(wf) else copy.deepcopy(wf)
    N = {n["name"]: n for n in wf["nodes"]}
    C = wf["connections"]
    sub = lambda js: js.replace("__URL__", url.rstrip("/")).replace("__TOKEN__", token)
    mx, my = N["Merge Context"]["position"]
    jx, jy = N["Judge Gate"]["position"]
    wf["nodes"].append(_code(TAG + "temat", sub(TOPIC_JS), [mx, my - 200]))
    wf["nodes"].append(_code(TAG + "kontrola", sub(REVIEW_JS), [jx, jy + 200]))
    C["Merge Context"] = {"main": [[{"node": TAG + "temat", "type": "main", "index": 0}]]}
    C[TAG + "temat"] = {"main": [[{"node": "Message a model", "type": "main", "index": 0}]]}
    C["Judge Gate"] = {"main": [[{"node": TAG + "kontrola", "type": "main", "index": 0}]]}
    C[TAG + "kontrola"] = {"main": [[{"node": "Script Passed?", "type": "main", "index": 0}]]}
    # „uruchom teraz” – webhook z tokenem, startuje to samo co poranny harmonogram
    trig = next((n for n in wf["nodes"] if n["type"].endswith("scheduleTrigger") and n["name"] in C), None)
    if trig:
        tx, ty = trig["position"]
        wid = str(uuid.uuid4())
        wf["nodes"].append({"parameters": {"httpMethod": "POST", "path": "agency-generator", "options": {}}, "id": str(uuid.uuid4()),
                            "name": TAG + "start", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [tx, ty - 360],
                            "webhookId": wid})
        wf["nodes"].append({"parameters": {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                            "conditions": [{"id": str(uuid.uuid4()), "leftValue": "={{ $json.body.token }}", "rightValue": token,
                                            "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"}, "options": {}},
                            "id": str(uuid.uuid4()), "name": TAG + "token OK?", "type": "n8n-nodes-base.if", "typeVersion": 2.2,
                            "position": [tx + 220, ty - 360]})
        C[TAG + "start"] = {"main": [[{"node": TAG + "token OK?", "type": "main", "index": 0}]]}
        C[TAG + "token OK?"] = {"main": [copy.deepcopy(C[trig["name"]]["main"][0]), []]}
    return wf


def put_body(wf, minimal=False):
    keep = ("executionOrder", "timezone", "errorWorkflow", "callerPolicy", "saveDataErrorExecution", "saveDataSuccessExecution",
            "saveManualExecutions", "saveExecutionProgress", "executionTimeout")
    s = wf.get("settings") or {}
    return {"name": wf["name"], "nodes": wf["nodes"], "connections": wf["connections"],
            "settings": {k: v for k, v in s.items() if k in keep} if minimal else s}
