"""
=============================================================
  APLICAÇÃO WEB — Recrutamento Semântico
  Autores: Nunes Ndala Samba · Manuel Alfredo Tchalocano
  Instituto Superior Politécnico da Huíla — ISPH
  Disciplina: Web Semântica · Docente: Faby Sapeth
=============================================================
  pip install flask rdflib owlrl flask-login
  python app.py  →  http://localhost:5000

  MÓDULOS ADICIONADOS:
  1. Autenticação      — login/logout, sessões, papéis (recrutador/candidato)
  2. Processo seleção  — etapas: triagem → entrevista → decisão + histórico
  3. Notificações      — email automático ao mudar estado (smtplib)
  4. Relatórios        — exportação CSV, gráfico de funil no dashboard
=============================================================
"""

from flask import (Flask, jsonify, request, render_template_string,
                   session, redirect, url_for, make_response)
from rdflib import Graph, Namespace, RDF, Literal, URIRef
from rdflib.namespace import XSD, RDFS, FOAF, OWL
from owlrl import DeductiveClosure, OWLRL_Semantics
from datetime import date, datetime
from functools import wraps
import os, uuid, csv, io, smtplib, hashlib
from email.mime.text import MIMEText

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "isph-recrutamento-2025-secret")

# ── Configuração de email (ajusta as variáveis de ambiente ou edita aqui) ──
MAIL_HOST   = os.environ.get("MAIL_HOST",   "smtp.gmail.com")
MAIL_PORT   = int(os.environ.get("MAIL_PORT", 587))
MAIL_USER   = os.environ.get("MAIL_USER",   "")   # o teu email
MAIL_PASS   = os.environ.get("MAIL_PASS",   "")   # app password
MAIL_FROM   = os.environ.get("MAIL_FROM",   "noreply@isph.recrutamento.ao")
MAIL_ENABLE = bool(MAIL_USER and MAIL_PASS)

OWL_PATH = os.path.join(os.path.dirname(__file__), "recrutamento.owl")

REC     = Namespace("http://www.semanticweb.org/isph/recrutamento#")
FOAF_NS = Namespace("http://xmlns.com/foaf/0.1/")

# ── Utilizadores hardcoded (em produção usa uma BD real) ──
# candidato_id deve corresponder ao ID do nó RDF do candidato na ontologia
USERS = {
    "recrutador": {
        "senha": hashlib.sha256("isph2025".encode()).hexdigest(),
        "papel": "recrutador",
        "nome": "Recrutador ISPH",
        "candidato_id": None,
    },
    "admin": {
        "senha": hashlib.sha256("admin123".encode()).hexdigest(),
        "papel": "recrutador",
        "nome": "Administrador",
        "candidato_id": None,
    },
    "candidato": {
        "senha": hashlib.sha256("cand123".encode()).hexdigest(),
        "papel": "candidato",
        "nome": "Nunes Ndala Samba",
        # ID do nó RDF correspondente na ontologia
        "candidato_id": "Candidato_NunesNdala",
    },
    "manuel": {
        "senha": hashlib.sha256("manuel123".encode()).hexdigest(),
        "papel": "candidato",
        "nome": "Manuel Alfredo Tchalocano",
        "candidato_id": "Candidato_ManuelAlfredo",
    },
}

# ── Etapas do processo de seleção ──
ETAPAS = ["Triagem", "Entrevista RH", "Entrevista Técnica", "Proposta", "Contratado", "Rejeitado"]

def carregar_e_inferir():
    grafo = Graph()
    grafo.parse(OWL_PATH, format="xml")
    DeductiveClosure(OWLRL_Semantics).expand(grafo)
    print(f"  [Reasoner OWL-RL] {len(grafo)} triplos apos inferencia.")
    return grafo

g = carregar_e_inferir()

PREFIXOS = """
    PREFIX rec: <http://www.semanticweb.org/isph/recrutamento#>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX xsd:  <http://www.w3.org/2001/XMLSchema#>
    PREFIX foaf: <http://xmlns.com/foaf/0.1/>
"""

def sparql(query):
    results = g.query(PREFIXOS + query)
    rows = []
    for row in results:
        d = {}
        for var in results.vars:
            val = row[var]
            if val is None:
                d[str(var)] = ""
            else:
                s = str(val)
                d[str(var)] = s.split("#")[-1] if "#" in s else s
        rows.append(d)
    return rows

def salvar():
    global g
    base = Graph()
    base.parse(OWL_PATH, format="xml")
    skip_ns = {"http://www.w3.org/2002/07/owl#", "http://www.w3.org/2000/01/rdf-schema#",
               "http://www.w3.org/1999/02/22-rdf-syntax-ns#"}
    for s, p, o in g:
        ns = str(p).rsplit("#", 1)[0] + "#"
        if ns not in skip_ns:
            base.add((s, p, o))
    base.serialize(destination=OWL_PATH, format="xml")
    g = carregar_e_inferir()

# ============================================================
#  AUTENTICAÇÃO — helpers
# ============================================================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            if request.is_json:
                return jsonify({"ok": False, "erro": "Não autenticado"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated

def recrutador_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("papel") != "recrutador":
            if request.is_json:
                return jsonify({"ok": False, "erro": "Sem permissão"}), 403
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated

# ============================================================
#  NOTIFICAÇÕES — email
# ============================================================
def enviar_email(destinatario, assunto, corpo):
    if not MAIL_ENABLE:
        print(f"  [Email simulado] Para: {destinatario} | Assunto: {assunto}")
        return
    try:
        msg = MIMEText(corpo, "plain", "utf-8")
        msg["Subject"] = assunto
        msg["From"]    = MAIL_FROM
        msg["To"]      = destinatario
        with smtplib.SMTP(MAIL_HOST, MAIL_PORT) as s:
            s.starttls()
            s.login(MAIL_USER, MAIL_PASS)
            s.sendmail(MAIL_FROM, [destinatario], msg.as_string())
    except Exception as e:
        print(f"  [Email erro] {e}")

def notificar_estado(candidatura_node):
    """Envia email ao candidato quando o estado da candidatura muda."""
    cand_node  = g.value(candidatura_node, REC.isSubmetidaPor)
    vaga_node  = g.value(candidatura_node, REC.refereVaga)
    email      = str(g.value(cand_node, REC.email) or "")
    nome       = str(g.value(cand_node, REC.nomeCompleto) or "Candidato")
    titulo     = str(g.value(vaga_node, REC.tituloVaga) or "vaga")
    estado     = str(g.value(candidatura_node, REC.estadoCandidatura) or "")
    etapa      = str(g.value(candidatura_node, REC.etapaSeleção) or "")
    if not email:
        return
    assunto = f"[ISPH Recrutamento] Actualização da tua candidatura — {titulo}"
    corpo = (
        f"Olá {nome},\n\n"
        f"A tua candidatura para a vaga '{titulo}' foi actualizada.\n\n"
        f"Estado: {estado}\n"
        + (f"Etapa: {etapa}\n" if etapa else "") +
        f"\nEntra em contacto connosco para mais informações.\n\n"
        f"Equipa de Recrutamento — ISPH\n"
    )
    enviar_email(email, assunto, corpo)

# ============================================================
#  HTML COMPLETO
# ============================================================
HTML_LOGIN = """
<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Acesso — Recrutamento Semântico ISPH</title>
<style>
:root{--bg:#0f1117;--card:#1a1d27;--border:#2a2d3e;--accent:#6c63ff;--green:#00d4aa;--text:#e0e0e0;--muted:#888;--red:#ff6b6b;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;}
.box{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:36px 40px;width:100%;max-width:440px;}
.logo{font-size:1.25rem;font-weight:700;color:#fff;margin-bottom:4px;}
.sub{font-size:.78rem;color:var(--muted);margin-bottom:24px;}
/* tabs */
.tabs{display:flex;gap:0;margin-bottom:28px;border-bottom:1px solid var(--border);}
.tab{flex:1;background:none;border:none;color:var(--muted);padding:10px;font-size:.88rem;cursor:pointer;border-bottom:2px solid transparent;transition:all .2s;}
.tab.active{color:var(--accent);border-bottom-color:var(--accent);font-weight:600;}
.pane{display:none;} .pane.active{display:block;}
label{display:block;font-size:.78rem;color:var(--muted);margin-bottom:5px;}
input,select{width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:9px 13px;border-radius:8px;font-size:.88rem;margin-bottom:14px;}
input:focus,select:focus{outline:none;border-color:var(--accent);}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px;}
.btn{width:100%;background:var(--accent);color:#fff;border:none;padding:11px;border-radius:8px;font-size:.92rem;font-weight:600;cursor:pointer;margin-top:4px;}
.btn:hover{opacity:.88;}
.btn-sec{background:var(--green);color:#000;}
.msg{padding:9px 13px;border-radius:8px;font-size:.82rem;margin-bottom:14px;}
.msg.erro{background:rgba(255,107,107,.1);border:1px solid var(--red);color:var(--red);}
.msg.ok{background:rgba(0,212,170,.1);border:1px solid var(--green);color:var(--green);}
.hint{font-size:.72rem;color:var(--muted);margin-top:16px;text-align:center;line-height:1.8;}
.divider{border:none;border-top:1px solid var(--border);margin:16px 0;}
.sec-title{font-size:.75rem;color:var(--accent);font-weight:600;text-transform:uppercase;letter-spacing:.06em;margin-bottom:10px;margin-top:4px;}
.comp-grid{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;}
.comp-grid label{display:flex;align-items:center;gap:5px;font-size:.8rem;color:var(--text);margin-bottom:0;cursor:pointer;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:5px 10px;}
.comp-grid input[type=checkbox]{width:auto;margin-bottom:0;accent-color:var(--accent);}
</style>
</head>
<body>
<div class="box">
  <div class="logo">🎓 Recrutamento Semântico</div>
  <p class="sub">Instituto Superior Politécnico da Huíla</p>

  <div class="tabs">
    <button class="tab active" onclick="trocarTab('login',this)">Entrar</button>
    <button class="tab" onclick="trocarTab('registar',this)">Criar Conta</button>
  </div>

  <!-- ===== TAB LOGIN ===== -->
  <div class="pane active" id="pane-login">
    {% if erro %}<div class="msg erro">{{ erro }}</div>{% endif %}
    <form method="POST" action="/login">
      <label>Utilizador</label>
      <input name="user" placeholder="ex: nunes.samba" required autofocus/>
      <label>Palavra-passe</label>
      <input name="senha" type="password" placeholder="••••••••" required/>
      <button class="btn" type="submit">Entrar</button>
    </form>
    <p class="hint">
      Contas de demonstração:<br>
      <strong>recrutador</strong> / isph2025 &nbsp;·&nbsp;
      <strong>candidato</strong> / cand123 &nbsp;·&nbsp;
      <strong>manuel</strong> / manuel123
    </p>
  </div>

  <!-- ===== TAB REGISTAR ===== -->
  <div class="pane" id="pane-registar">
    <div id="reg-msg"></div>

    <div class="sec-title">Dados de Acesso</div>
    <div class="row">
      <div>
        <label>Utilizador *</label>
        <input id="r-user" placeholder="ex: joao.silva"/>
      </div>
      <div>
        <label>Palavra-passe *</label>
        <input id="r-senha" type="password" placeholder="••••••••"/>
      </div>
    </div>

    <hr class="divider"/>
    <div class="sec-title">Perfil Pessoal</div>
    <label>Nome Completo *</label>
    <input id="r-nome" placeholder="Ex: João Silva"/>
    <div class="row">
      <div>
        <label>Email *</label>
        <input id="r-email" type="email" placeholder="joao@email.com"/>
      </div>
      <div>
        <label>Telefone</label>
        <input id="r-tel" placeholder="+244 9xx xxx xxx"/>
      </div>
    </div>

    <hr class="divider"/>
    <div class="sec-title">Formação Académica</div>
    <div class="row">
      <div>
        <label>Grau</label>
        <select id="r-grau">
          <option value="">— Selecciona —</option>
          <option>Licenciatura</option>
          <option>Mestrado</option>
          <option>Doutoramento</option>
          <option>Bacharelato</option>
          <option>Técnico Médio</option>
        </select>
      </div>
      <div>
        <label>Área</label>
        <input id="r-area" placeholder="Ex: Informática"/>
      </div>
    </div>
    <label>Instituição</label>
    <input id="r-inst" placeholder="Ex: ISPH"/>

    <hr class="divider"/>
    <div class="sec-title">Experiência</div>
    <div class="row">
      <div>
        <label>Anos de Experiência</label>
        <input id="r-exp" type="number" min="0" placeholder="0"/>
      </div>
      <div>
        <label>Cargo Actual / Último</label>
        <input id="r-cargo" placeholder="Ex: Técnico de TI"/>
      </div>
    </div>

    <hr class="divider"/>
    <div class="sec-title">Competências</div>
    <div class="comp-grid" id="reg-comps">
      <span style="color:var(--muted);font-size:.8rem;">A carregar…</span>
    </div>

    <button class="btn btn-sec" onclick="registar()">Criar Conta e Entrar</button>
  </div>
</div>

<script>
function trocarTab(id, btn) {
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.pane').forEach(p=>p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('pane-'+id).classList.add('active');
  if (id==='registar') carregarComps();
}

let compsCarregadas = false;
async function carregarComps() {
  if (compsCarregadas) return;
  try {
    const comps = await fetch('/api/competencias-pub').then(r=>r.json());
    const el = document.getElementById('reg-comps');
    if (!comps.length) { el.innerHTML='<span style="color:var(--muted);font-size:.8rem;">Sem competências disponíveis.</span>'; return; }
    el.innerHTML = comps.map(c=>`
      <label><input type="checkbox" value="${c.id}"> ${c.nome}</label>`).join('');
    compsCarregadas = true;
  } catch(e) {
    document.getElementById('reg-comps').innerHTML='<span style="color:var(--muted);font-size:.8rem;">Não foi possível carregar.</span>';
  }
}

async function registar() {
  const user  = document.getElementById('r-user').value.trim().toLowerCase().replace(/\\s+/g,'.');
  const senha = document.getElementById('r-senha').value;
  const nome  = document.getElementById('r-nome').value.trim();
  const email = document.getElementById('r-email').value.trim();
  const msgEl = document.getElementById('reg-msg');

  if (!user || !senha || !nome || !email) {
    msgEl.innerHTML='<div class="msg erro">Utilizador, palavra-passe, nome e email são obrigatórios.</div>'; return;
  }
  if (senha.length < 4) {
    msgEl.innerHTML='<div class="msg erro">A palavra-passe deve ter pelo menos 4 caracteres.</div>'; return;
  }

  const comps = [...document.querySelectorAll('#reg-comps input:checked')].map(i=>i.value);
  const body = {
    user, senha,
    nome, email,
    telefone: document.getElementById('r-tel').value,
    anos:     document.getElementById('r-exp').value || '0',
    grau:     document.getElementById('r-grau').value,
    area:     document.getElementById('r-area').value,
    instituicao: document.getElementById('r-inst').value,
    cargoExperiencia: document.getElementById('r-cargo').value,
    competencias: comps,
  };

  const d = await fetch('/registar', {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)
  }).then(r=>r.json());

  if (d.ok) {
    msgEl.innerHTML='<div class="msg ok">✓ Conta criada! A entrar…</div>';
    setTimeout(() => location.href='/', 800);
  } else {
    msgEl.innerHTML=`<div class="msg erro">${d.erro}</div>`;
  }
}
</script>
</body>
</html>
"""

HTML = r"""
<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Recrutamento Semântico — ISPH</title>
<style>
:root{
  --bg:#0f1117;--card:#1a1d27;--card2:#20243a;--border:#2a2d3e;
  --accent:#6c63ff;--green:#00d4aa;--red:#ff6b6b;--yellow:#ffc107;
  --text:#e0e0e0;--muted:#888;
}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);
font-family:'Segoe UI',sans-serif;min-height:100vh;}
header{background:linear-gradient(135deg,var(--accent),#9b59b6);
padding:20px 32px;
display:flex;align-items:center;
justify-content:space-between;}
header h1{font-size:1.4rem;font-weight:700;}
header p{font-size:.78rem;opacity:.8;margin-top:3px;}
.header-right{display:flex;align-items:center;gap:14px;}
.user-badge{background:rgba(255,255,255,.15);
padding:5px 14px;border-radius:20px;
font-size:.8rem;}
.btn-logout{background:rgba(255,255,255,.2);
border:none;color:#fff;
padding:6px 14px;
border-radius:20px;
cursor:pointer;font-size:.8rem;}
nav{display:flex;gap:8px;padding:14px 32px;background:var(--card);border-bottom:1px solid var(--border);flex-wrap:wrap;}
nav button{background:transparent;border:1px solid var(--border);color:var(--muted);padding:7px 18px;border-radius:20px;cursor:pointer;font-size:.84rem;transition:all .2s;}
nav button:hover,nav button.active{background:var(--accent);border-color:var(--accent);color:#fff;}
nav button.rec-only{display:none;}
nav button.cand-only{display:none;}
main{padding:24px 32px;max-width:1200px;margin:0 auto;}
.section{display:none;} .section.active{display:block;}
h2{font-size:1.05rem;color:var(--green);margin-bottom:18px;}
h3{font-size:.95rem;color:var(--accent);margin:20px 0 12px;}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;margin-bottom:28px;}
.stat{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px;text-align:center;}
.stat .num{font-size:2rem;font-weight:700;color:var(--accent);}
.stat .lbl{font-size:.75rem;color:var(--muted);margin-top:4px;}
.vagas-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;}
.vaga-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px;transition:border-color .2s;}
.vaga-card:hover{border-color:var(--accent);}
.vaga-card h4{font-size:1rem;margin-bottom:8px;}
.vaga-card .empresa{font-size:.8rem;color:var(--accent);margin-bottom:12px;}
.vaga-card .info{font-size:.82rem;color:var(--muted);line-height:1.8;}
.vaga-card .tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px;}
.btn-candidatar{width:100%;margin-top:14px;background:var(--accent);color:#fff;border:none;padding:9px;border-radius:8px;cursor:pointer;font-size:.85rem;transition:opacity .2s;}
.btn-candidatar:hover{opacity:.85;}
.tbl-wrap{overflow-x:auto;border-radius:10px;border:1px solid var(--border);}
table{width:100%;border-collapse:collapse;font-size:.84rem;}
thead tr{background:var(--card2);}
th{padding:11px 14px;text-align:left;color:var(--muted);font-weight:600;}
td{padding:10px 14px;border-top:1px solid var(--border);}
tr:hover td{background:rgba(108,99,255,.05);}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:.73rem;font-weight:600;}
.b-green{background:rgba(0,212,170,.15);color:var(--green);}
.b-yellow{background:rgba(255,193,7,.15);color:var(--yellow);}
.b-red{background:rgba(255,107,107,.15);color:var(--red);}
.b-blue{background:rgba(108,99,255,.2);color:#a599ff;}
.b-gray{background:rgba(136,135,128,.15);color:var(--muted);}
.b-purple{background:rgba(155,89,182,.2);color:#c39bd3;}
.match-bar{height:6px;border-radius:3px;background:var(--border);margin-top:5px;}
.match-fill{height:100%;border-radius:3px;background:linear-gradient(90deg,var(--accent),var(--green));}
.form-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:24px;margin-bottom:20px;}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:16px;}
.form-group{margin-bottom:16px;}
.form-group label{display:block;font-size:.8rem;color:var(--muted);margin-bottom:6px;}
.form-group input,.form-group select,.form-group textarea{width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:9px 12px;border-radius:8px;font-size:.88rem;}
.form-group textarea{resize:vertical;min-height:80px;}
.form-group input:focus,.form-group select:focus{outline:none;border-color:var(--accent);}
.checkbox-group{display:flex;flex-wrap:wrap;gap:10px;margin-top:6px;}
.checkbox-group label{display:flex;align-items:center;gap:6px;font-size:.82rem;color:var(--text);cursor:pointer;}
.btn-submit{background:var(--accent);color:#fff;border:none;padding:10px 28px;border-radius:8px;cursor:pointer;font-size:.9rem;font-weight:600;transition:opacity .2s;}
.btn-submit:hover{opacity:.85;}
.btn-danger{background:var(--red);color:#fff;border:none;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:.78rem;}
.btn-sm{background:var(--card2);color:var(--text);border:1px solid var(--border);padding:5px 12px;border-radius:6px;cursor:pointer;font-size:.78rem;margin-right:4px;}
.search-bar{display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap;}
.search-bar input,.search-bar select{background:var(--card);border:1px solid var(--border);color:var(--text);padding:9px 14px;border-radius:8px;font-size:.87rem;flex:1;min-width:160px;}
.search-bar button{background:var(--accent);color:#fff;border:none;padding:9px 20px;border-radius:8px;cursor:pointer;font-size:.87rem;}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:100;align-items:center;justify-content:center;}
.modal-overlay.open{display:flex;}
.modal{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:28px;width:100%;max-width:520px;max-height:90vh;overflow-y:auto;}
.modal h3{margin-bottom:18px;color:var(--accent);}
.modal-close{float:right;background:none;border:none;color:var(--muted);font-size:1.3rem;cursor:pointer;margin-top:-4px;}
.toast{position:fixed;bottom:24px;right:24px;background:var(--green);color:#000;padding:12px 22px;border-radius:10px;font-size:.88rem;font-weight:600;z-index:200;display:none;animation:fadein .3s;}
@keyframes fadein{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.empty{text-align:center;padding:40px;color:var(--muted);}
.semantic-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-bottom:20px;}
.semantic-card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:16px;}
.semantic-card h4{font-size:.95rem;margin-bottom:6px;}
.semantic-card .meta{font-size:.8rem;color:var(--muted);line-height:1.7;}
.semantic-card .score{font-size:1.6rem;font-weight:700;color:var(--accent);margin:8px 0 4px;}
.semantic-card .semantic-list{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;}
.semantic-actions{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px;}
.semantic-actions select{background:var(--card);border:1px solid var(--border);color:var(--text);padding:9px 14px;border-radius:8px;font-size:.87rem;min-width:220px;}

/* FUNIL DE SELEÇÃO */
.funil{display:flex;flex-direction:column;gap:6px;margin-bottom:28px;}
.funil-row{display:flex;align-items:center;gap:12px;}
.funil-label{font-size:.82rem;color:var(--muted);width:140px;text-align:right;}
.funil-bar-wrap{flex:1;background:var(--border);border-radius:4px;height:22px;position:relative;}
.funil-bar{height:100%;border-radius:4px;background:linear-gradient(90deg,var(--accent),var(--green));transition:width .6s;}
.funil-count{font-size:.8rem;color:var(--text);margin-left:8px;min-width:24px;}

/* HISTÓRICO ETAPAS */
.etapas-timeline{display:flex;flex-direction:column;gap:6px;margin:14px 0;}
.etapa-item{display:flex;align-items:center;gap:10px;font-size:.82rem;}
.etapa-dot{width:10px;height:10px;border-radius:50%;background:var(--accent);flex-shrink:0;}
.etapa-dot.done{background:var(--green);}
.etapa-dot.rejected{background:var(--red);}

/* BOTÃO EXPORTAR */
.btn-export{background:var(--green);color:#000;border:none;padding:8px 18px;border-radius:8px;cursor:pointer;font-size:.84rem;font-weight:600;margin-left:10px;}

footer{text-align:center;padding:16px;color:var(--muted);font-size:.74rem;border-top:1px solid var(--border);margin-top:40px;}
</style>
</head>
<body>

<header>
  <div>
    <h1> Recrutamento Semântico</h1>
    <p>Instituto Superior Politécnico da Huíla · Web Semântica </p>
  </div>
  <div class="header-right">
    <span class="user-badge" id="user-badge">—</span>
    <button class="btn-logout" onclick="location.href='/logout'">Sair</button>
  </div>
</header>

<nav>
  <button class="active" onclick="nav('dashboard',this)">Dashboard</button>
  <button onclick="nav('vagas',this)">Vagas</button>
  <button class="cand-only" onclick="nav('minhas-cands',this)">As Minhas Candidaturas</button>
  <button class="rec-only" onclick="nav('candidatos',this)">Candidatos</button>
  <button class="rec-only" onclick="nav('candidaturas',this)">Candidaturas</button>
  <button class="rec-only" onclick="nav('selecao',this)">Processo de Selecção</button>
  <button onclick="nav('analise',this)">Análise Semântica</button>
  <button class="rec-only" onclick="nav('empresas',this)">Empresas</button>
  <button class="rec-only" onclick="nav('relatorios',this)">Relatórios</button>
  <button class="rec-only" onclick="nav('cadastrar',this)">+ Cadastrar</button>
</nav>

<main>

<!-- ===== DASHBOARD ===== -->
<div class="section active" id="s-dashboard">
  <h2>Visão Geral</h2>
  <div class="stats">
    <div class="stat"><div class="num" id="n-cand">—</div><div class="lbl">Candidatos</div></div>
    <div class="stat"><div class="num" id="n-vagas">—</div><div class="lbl">Vagas</div></div>
    <div class="stat"><div class="num" id="n-emp">—</div><div class="lbl">Empresas</div></div>
    <div class="stat"><div class="num" id="n-comp">—</div><div class="lbl">Competências</div></div>
    <div class="stat"><div class="num" id="n-cands">—</div><div class="lbl">Candidaturas</div></div>
  </div>

  <h3>Funil de Selecção</h3>
  <div class="funil" id="funil-dash"></div>

  <h3>Candidaturas Recentes</h3>
  <div class="tbl-wrap">
    <table><thead><tr><th>Candidato</th><th>Vaga</th><th>Empresa</th><th>Estado</th><th>Compatibilidade</th></tr></thead>
    <tbody id="dash-tbody"></tbody></table>
  </div>
</div>

<!-- ===== AS MINHAS CANDIDATURAS ===== -->
<div class="section" id="s-minhas-cands">
  <h2>As Minhas Candidaturas</h2>
  <p style="color:var(--muted);font-size:.85rem;margin-bottom:20px;">Acompanha o estado de cada candidatura que submeteste.</p>
  <div id="minhas-cands-lista"><p class="empty">A carregar…</p></div>
</div>

<!-- ===== VAGAS ===== -->
<div class="section" id="s-vagas">
  <h2>Vagas Disponíveis</h2>
  <div class="search-bar">
    <input id="pesq-vaga" placeholder="Pesquisar por título ou empresa…" oninput="filtrarVagas()"/>
    <select id="pesq-local" onchange="filtrarVagas()">
      <option value="">Todas as localidades</option>
    </select>
    <select id="pesq-modal" onchange="filtrarVagas()">
      <option value="">Todas as modalidades</option>
      <option>Presencial</option><option>Remoto</option><option>Híbrido</option>
    </select>
  </div>
  <div class="vagas-grid" id="vagas-grid"></div>
</div>

<!-- ===== CANDIDATOS ===== -->
<div class="section" id="s-candidatos">
  <h2>Candidatos Registados</h2>
  <div class="search-bar">
    <input id="pesq-cand" placeholder="Pesquisar por nome ou competência…" oninput="filtrarCandidatos()"/>
  </div>
  <div class="tbl-wrap">
    <table><thead><tr><th>Nome</th><th>Email</th><th>Experiência</th><th>Formação</th><th>Competências</th><th>Ações</th></tr></thead>
    <tbody id="cand-tbody"></tbody></table>
  </div>
</div>

<!-- ===== CANDIDATURAS ===== -->
<div class="section" id="s-candidaturas">
  <h2>Gestão de Candidaturas</h2>
  <div class="search-bar">
    <select id="pesq-estado" onchange="carregarCandidaturas()">
      <option value="">Todos os estados</option>
      <option>Em análise</option><option>Aprovada</option>
      <option>Rejeitada</option><option>Em entrevista</option><option>Contratado</option>
    </select>
    <button class="btn-export" onclick="exportarCSV()">⬇ Exportar CSV</button>
  </div>
  <div class="tbl-wrap">
    <table><thead><tr><th>Candidato</th><th>Vaga</th><th>Empresa</th><th>Data</th><th>Estado</th><th>Compatibilidade</th><th>Ações</th></tr></thead>
    <tbody id="cands-tbody"></tbody></table>
  </div>
</div>

<!-- ===== PROCESSO DE SELECÇÃO ===== -->
<div class="section" id="s-selecao">
  <h2>Processo de Selecção</h2>
  <p style="font-size:.83rem;color:var(--muted);margin-bottom:18px;">
    Avança as candidaturas pelas etapas formais de selecção e regista o historial de cada decisão.
  </p>

  <div class="search-bar">
    <select id="sel-vaga-filtro" onchange="carregarSelecao()">
      <option value="">Todas as vagas</option>
    </select>
    <select id="sel-etapa-filtro" onchange="carregarSelecao()">
      <option value="">Todas as etapas</option>
      <option>Triagem</option><option>Entrevista RH</option><option>Entrevista Técnica</option>
      <option>Proposta</option><option>Contratado</option><option>Rejeitado</option>
    </select>
  </div>

  <div class="tbl-wrap">
    <table>
      <thead><tr><th>Candidato</th><th>Vaga</th><th>Etapa actual</th><th>Score</th><th>Avançar etapa</th><th>Histórico</th></tr></thead>
      <tbody id="sel-tbody"></tbody>
    </table>
  </div>
</div>

<!-- ===== ANÁLISE SEMÂNTICA ===== -->
<div class="section" id="s-analise">
  <h2>Análise Semântica</h2>
  <div class="semantic-actions">
    <select id="analise-vaga" onchange="carregarAnalise()">
      <option value="">Todas as vagas</option>
    </select>
  </div>

  <h3>Matching candidato-vaga</h3>
  <div class="semantic-grid" id="match-grid"></div>

  <h3>Gaps de competências e experiência</h3>
  <div class="tbl-wrap">
    <table><thead><tr><th>Candidato</th><th>Vaga</th><th>Competências em falta</th><th>Experiência (cand./mín.)</th><th>Estado</th></tr></thead>
    <tbody id="gap-tbody"></tbody></table>
  </div>

  <h3>Vocabulários externos (ESCO / FOAF)</h3>
  <div class="tbl-wrap">
    <table><thead><tr><th>Recurso</th><th>Tipo</th><th>Alinhamento semântico</th></tr></thead>
    <tbody id="vocab-tbody"></tbody></table>
  </div>

  <h3>Factos inferidos pelo Reasoner OWL-RL</h3>
  <p style="font-size:.82rem;color:var(--muted);margin-bottom:12px;">
    Triplos <em>deduzidos automaticamente</em> pela inferencia &mdash; nao existiam na ontologia original, foram derivados pelas regras OWL.
  </p>
  <div class="tbl-wrap">
    <table><thead><tr><th>Sujeito</th><th>Propriedade inferida</th><th>Objecto</th></tr></thead>
    <tbody id="inf-tbody"></tbody></table>
  </div>
</div>

<!-- ===== EMPRESAS ===== -->
<div class="section" id="s-empresas">
  <h2>Empresas Parceiras</h2>
  <div class="tbl-wrap">
    <table><thead><tr><th>Empresa</th><th>Sector</th><th>Localização</th><th>Vagas Publicadas</th></tr></thead>
    <tbody id="emp-tbody"></tbody></table>
  </div>
</div>

<!-- ===== RELATÓRIOS ===== -->
<div class="section" id="s-relatorios">
  <h2>Relatórios</h2>

  <h3>Funil completo de selecção</h3>
  <div class="funil" id="funil-rel" style="max-width:600px;"></div>

  <h3>Exportação de dados</h3>
  <div class="form-card" style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;">
    <div>
      <p style="font-size:.85rem;margin-bottom:10px;">Candidaturas completas (todas as colunas)</p>
      <button class="btn-export" onclick="exportarCSV()">⬇ Exportar CSV — Candidaturas</button>
    </div>
    <div>
      <p style="font-size:.85rem;margin-bottom:10px;">Ranking semântico de candidatos por vaga</p>
      <button class="btn-export" onclick="exportarRanking()">⬇ Exportar CSV — Ranking</button>
    </div>
  </div>

  <h3>Candidaturas por estado</h3>
  <div class="tbl-wrap">
    <table><thead><tr><th>Estado</th><th>Total</th><th>% do total</th></tr></thead>
    <tbody id="rel-estado-tbody"></tbody>
  </table></div>

  <h3>Top candidatos por score semântico</h3>
  <div class="tbl-wrap">
    <table><thead><tr><th>#</th><th>Candidato</th><th>Vaga</th><th>Score</th><th>Compatível</th></tr></thead>
    <tbody id="rel-top-tbody"></tbody>
  </table></div>
</div>

<!-- ===== CADASTRAR ===== -->
<div class="section" id="s-cadastrar">
  <h2>Cadastrar Novo Registo</h2>

  <h3>👤 Novo Candidato</h3>
  <div class="form-card">
    <div class="form-row">
      <div class="form-group"><label>Nome Completo *</label><input id="c-nome" placeholder="Ex: João Silva"/></div>
      <div class="form-group"><label>Email *</label><input id="c-email" type="email" placeholder="joao@email.com"/></div>
    </div>
    <div class="form-row">
      <div class="form-group"><label>Telefone</label><input id="c-tel" placeholder="+244 9xx xxx xxx"/></div>
      <div class="form-group"><label>Anos de Experiência</label><input id="c-exp" type="number" min="0" placeholder="0"/></div>
    </div>
    <div class="form-row">
      <div class="form-group"><label>Grau de Formação</label>
        <select id="c-grau">
          <option>Licenciatura</option><option>Mestrado</option><option>Doutoramento</option><option>Bacharelato</option>
        </select>
      </div>
      <div class="form-group"><label>Área de Formação</label><input id="c-area" placeholder="Ex: Engenharia Informática"/></div>
    </div>
    <div class="form-group"><label>Instituição de Ensino</label><input id="c-inst" placeholder="Ex: ISPH"/></div>
    <div class="form-row">
      <div class="form-group"><label>Cargo mais recente</label><input id="c-cargo" placeholder="Ex: Desenvolvedor Backend"/></div>
      <div class="form-group"><label>Empresa da experiência</label><input id="c-empresa-exp" placeholder="Ex: TechAngola Lda."/></div>
    </div>
    <div class="form-group">
      <label>Competências (selecciona todas as que possui)</label>
      <div class="checkbox-group" id="comp-check-cand"></div>
    </div>
    <button class="btn-submit" onclick="cadastrarCandidato()">Registar Candidato</button>
  </div>

  <h3>💼 Nova Vaga</h3>
  <div class="form-card">
    <div class="form-row">
      <div class="form-group"><label>Título da Vaga *</label><input id="v-titulo" placeholder="Ex: Desenvolvedor Backend"/></div>
      <div class="form-group"><label>Empresa *</label>
        <select id="v-empresa"><option value="">— Selecciona a empresa —</option></select>
      </div>
    </div>
    <div class="form-row">
      <div class="form-group"><label>Local</label><input id="v-local" placeholder="Ex: Luanda"/></div>
      <div class="form-group"><label>Modalidade</label>
        <select id="v-modal"><option>Presencial</option><option>Remoto</option><option>Híbrido</option></select>
      </div>
    </div>
    <div class="form-row">
      <div class="form-group"><label>Salário (AOA)</label><input id="v-salario" type="number" placeholder="Ex: 350000"/></div>
      <div class="form-group"><label>Data de Publicação</label><input id="v-data" type="date"/></div>
    </div>
    <div class="form-group"><label>Descrição</label><textarea id="v-desc" placeholder="Descreve a vaga…"></textarea></div>
    <div class="form-group"><label>Experiência mínima (anos)</label><input id="v-exp-min" type="number" min="0" placeholder="0"/></div>
    <div class="form-group">
      <label>Competências Exigidas</label>
      <div class="checkbox-group" id="comp-check-vaga"></div>
    </div>
    <button class="btn-submit" onclick="cadastrarVaga()">Publicar Vaga</button>
  </div>

  <h3>🏢 Nova Empresa</h3>
  <div class="form-card">
    <div class="form-row">
      <div class="form-group"><label>Nome da Empresa *</label><input id="e-nome" placeholder="Ex: TechAngola Lda."/></div>
      <div class="form-group"><label>Sector de Actividade</label><input id="e-sector" placeholder="Ex: Tecnologias de Informação"/></div>
    </div>
    <div class="form-group"><label>Localização</label><input id="e-local" placeholder="Ex: Luanda, Angola"/></div>
    <button class="btn-submit" onclick="cadastrarEmpresa()">Registar Empresa</button>
  </div>

  <h3>⚙️ Nova Competência</h3>
  <div class="form-card">
    <div class="form-row">
      <div class="form-group"><label>Nome da Competência *</label><input id="k-nome" placeholder="Ex: React, Liderança…"/></div>
      <div class="form-group"><label>Tipo</label>
        <select id="k-tipo"><option value="CompetenciaTecnica">Técnica</option><option value="CompetenciaComportamental">Comportamental</option></select>
      </div>
    </div>
    <div class="form-row">
      <div class="form-group"><label>Nível</label>
        <select id="k-nivel"><option>Básico</option><option>Intermédio</option><option>Avançado</option><option>Especialista</option></select>
      </div>
      <div class="form-group"><label>Categoria</label><input id="k-cat" placeholder="Ex: Linguagem de Programação"/></div>
    </div>
    <button class="btn-submit" onclick="cadastrarCompetencia()">Adicionar Competência</button>
  </div>
</div>

</main>

<!-- MODAL CANDIDATURA -->
<div class="modal-overlay" id="modal-cand">
  <div class="modal" style="max-width:600px;">
    <button class="modal-close" onclick="fecharModal()">✕</button>
    <h3 id="modal-vaga-titulo">Candidatar-se</h3>

    <!-- Detalhes da vaga -->
    <div id="modal-vaga-detalhes" style="background:var(--bg);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:18px;font-size:.84rem;line-height:1.8;">
      <div style="color:var(--muted)">A carregar detalhes…</div>
    </div>

    <!-- Match info (só candidatos) -->
    <div id="modal-match-info" style="margin-bottom:14px;"></div>

    <!-- Carta de motivação -->
    <div class="form-group" id="modal-carta-wrap">
      <label>Carta de Motivação <span style="color:var(--muted);font-size:.76rem;">(opcional, mas recomendada)</span></label>
      <textarea id="modal-carta" rows="5" placeholder="Apresenta-te e explica porque és o candidato ideal para esta vaga…" style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:10px 14px;border-radius:8px;font-size:.86rem;resize:vertical;"></textarea>
    </div>

    <!-- Seletor de candidato (apenas recrutadores) -->
    <div class="form-group" id="modal-sel-cand-wrap" style="display:none;">
      <label>Submeter em nome de</label>
      <select id="modal-candidato" style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:9px 12px;border-radius:8px;font-size:.88rem;">
        <option value="">— Selecciona o candidato —</option>
      </select>
    </div>

    <div id="modal-aviso-dup" style="display:none;background:rgba(255,193,7,.1);border:1px solid var(--yellow);color:var(--yellow);padding:10px 14px;border-radius:8px;font-size:.83rem;margin-bottom:14px;">
      ⚠️ Já submeteste uma candidatura para esta vaga.
    </div>

    <button class="btn-submit" id="modal-btn-submeter" onclick="submeterCandidatura()" style="width:100%">
      Submeter Candidatura
    </button>
  </div>
</div>

<!-- MODAL HISTÓRICO ETAPAS -->
<div class="modal-overlay" id="modal-hist">
  <div class="modal">
    <button class="modal-close" onclick="fecharModalHist()">✕</button>
    <h3>Histórico de Selecção</h3>
    <p id="hist-cand-nome" style="color:var(--muted);font-size:.88rem;margin-bottom:14px;"></p>
    <div class="etapas-timeline" id="hist-timeline"></div>
  </div>
</div>

<!-- TOAST -->
<div class="toast" id="toast"></div>

<footer>Nunes Ndala Samba &amp; Manuel Alfredo Tchalocano · ISPH 2025 · Web Semântica</footer>

<script>
let vagasData = [];
let candidatosData = [];
let vagaSelecionada = null;
let papelActual = '';
let candidatoIdActual = null;

// ── init ──
(async () => {
  const info = await fetch('/api/me').then(r=>r.json());
  papelActual = info.papel;
  candidatoIdActual = info.candidatoId || null;
  document.getElementById('user-badge').textContent = info.nome + ' (' + info.papel + ')';
  if (info.papel === 'recrutador') {
    document.querySelectorAll('.rec-only').forEach(b => b.style.display = 'inline-block');
  }
  if (info.papel === 'candidato') {
    document.querySelectorAll('.cand-only').forEach(b => b.style.display = 'inline-block');
    // Esconde secções irrelevantes para candidatos na nav
    document.querySelectorAll('.rec-only').forEach(b => b.style.display = 'none');
  }
  carregarDashboard();
})();

// ── navegação ──
function nav(id, btn) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
  document.getElementById('s-' + id).classList.add('active');
  btn.classList.add('active');
  if (id==='dashboard')    carregarDashboard();
  if (id==='vagas')        carregarVagas();
  if (id==='candidatos')   carregarCandidatos();
  if (id==='candidaturas') carregarCandidaturas();
  if (id==='selecao')      carregarSelecao();
  if (id==='analise')      carregarAnalise();
  if (id==='empresas')     carregarEmpresas();
  if (id==='relatorios')   carregarRelatorios();
  if (id==='cadastrar')    carregarFormularios();
  if (id==='minhas-cands') carregarMinhasCandidaturas();
}

function toast(msg, cor='var(--green)') {
  const t = document.getElementById('toast');
  t.textContent = msg; t.style.background = cor;
  t.style.display = 'block';
  setTimeout(() => t.style.display='none', 3200);
}

function badge(txt) {
  const m = {
    'Em análise':'b-yellow','Aprovada':'b-green','Rejeitada':'b-red',
    'Em entrevista':'b-blue','Contratado':'b-green','Sem gap ✓':'b-green',
    'Compatível':'b-green','Com gaps':'b-yellow',
    'Triagem':'b-gray','Entrevista RH':'b-blue','Entrevista Técnica':'b-blue',
    'Proposta':'b-purple','Rejeitado':'b-red'
  };
  return `<span class="badge ${m[txt]||'b-gray'}">${txt}</span>`;
}
function barra(v) {
  const p = Math.round(parseFloat(v)*100);
  return `<div>${p}%<div class="match-bar"><div class="match-fill" style="width:${p}%"></div></div></div>`;
}

// ── DASHBOARD ──
async function carregarDashboard() {
  const s = await fetch('/api/stats').then(r=>r.json());
  document.getElementById('n-cand').textContent  = s.candidatos;
  document.getElementById('n-vagas').textContent = s.vagas;
  document.getElementById('n-emp').textContent   = s.empresas;
  document.getElementById('n-comp').textContent  = s.competencias;
  document.getElementById('n-cands').textContent = s.candidaturas;

  // Funil
  renderFunil('funil-dash', s.funil || {});

  const rows = await fetch('/api/candidaturas').then(r=>r.json());
  document.getElementById('dash-tbody').innerHTML = rows.length
    ? rows.slice(0,8).map(r=>`<tr>
        <td>${r.nomeCandidato||'—'}</td>
        <td>${r.tituloVaga||'—'}</td>
        <td>${r.nomeEmpresa||'—'}</td>
        <td>${badge(r.estado)}</td>
        <td>${r.pontuacao ? barra(r.pontuacao) : '—'}</td>
      </tr>`).join('')
    : '<tr><td colspan="5" class="empty">Sem candidaturas ainda.</td></tr>';
}

function renderFunil(elId, funil) {
  const etapas = ['Triagem','Entrevista RH','Entrevista Técnica','Proposta','Contratado','Rejeitado'];
  const max = Math.max(...Object.values(funil), 1);
  const el = document.getElementById(elId);
  if (!el) return;
  el.innerHTML = etapas.map(e => {
    const n = funil[e] || 0;
    const pct = Math.round(n / max * 100);
    return `<div class="funil-row">
      <span class="funil-label">${e}</span>
      <div class="funil-bar-wrap"><div class="funil-bar" style="width:${pct}%"></div></div>
      <span class="funil-count">${n}</span>
    </div>`;
  }).join('');
}

// ── VAGAS ──
async function carregarVagas() {
  vagasData = await fetch('/api/vagas').then(r=>r.json());
  const locais = [...new Set(vagasData.map(v=>v.local).filter(Boolean))];
  const sel = document.getElementById('pesq-local');
  sel.innerHTML = '<option value="">Todas as localidades</option>' +
    locais.map(l=>`<option>${l}</option>`).join('');
  renderVagas(vagasData);
}
function filtrarVagas() {
  const txt   = document.getElementById('pesq-vaga').value.toLowerCase();
  const local = document.getElementById('pesq-local').value;
  const modal = document.getElementById('pesq-modal').value;
  renderVagas(vagasData.filter(v =>
    (!txt   || v.titulo?.toLowerCase().includes(txt) || v.empresa?.toLowerCase().includes(txt)) &&
    (!local || v.local === local) &&
    (!modal || v.modalidade === modal)
  ));
}
function renderVagas(list) {
  const grid = document.getElementById('vagas-grid');
  if (!list.length) { grid.innerHTML='<p class="empty">Nenhuma vaga encontrada.</p>'; return; }
  grid.innerHTML = list.map(v=>`
    <div class="vaga-card">
      <h4>${v.titulo||'—'}</h4>
      <div class="empresa">🏢 ${v.empresa||'—'}</div>
      <div class="info">
        📍 ${v.local||'—'}<br>💻 ${v.modalidade||'—'}<br>
        💰 ${v.salario ? Number(v.salario).toLocaleString('pt-AO')+' AOA' : 'A definir'}<br>
        📅 ${v.data||'—'}
      </div>
      <div class="tags">${(v.competencias||'').split(',').filter(Boolean).map(c=>`<span class="badge b-blue">${c.trim()}</span>`).join('')}</div>
      <button class="btn-candidatar" onclick="abrirModal('${v.id}','${(v.titulo||'').replace(/'/g,"\\'")}')">Candidatar-me</button>
    </div>`).join('');
}

// ── CANDIDATOS ──
async function carregarCandidatos() {
  candidatosData = await fetch('/api/candidatos').then(r=>r.json());
  renderCandidatos(candidatosData);
}
function filtrarCandidatos() {
  const txt = document.getElementById('pesq-cand').value.toLowerCase();
  renderCandidatos(candidatosData.filter(c =>
    c.nome?.toLowerCase().includes(txt) || c.competencias?.toLowerCase().includes(txt)
  ));
}
function renderCandidatos(list) {
  const tbody = document.getElementById('cand-tbody');
  if (!list.length) { tbody.innerHTML='<tr><td colspan="6" class="empty">Sem candidatos.</td></tr>'; return; }
  tbody.innerHTML = list.map(c=>`<tr>
    <td><strong>${c.nome||'—'}</strong></td>
    <td>${c.email||'—'}</td>
    <td>${c.anos||'0'} anos</td>
    <td>${c.grau||'—'} em ${c.area||'—'}</td>
    <td>${(c.competencias||'').split(',').filter(Boolean).map(k=>`<span class="badge b-blue">${k.trim()}</span>`).join(' ')}</td>
    <td>${papelActual==='recrutador' ? `<button class="btn-danger" onclick="eliminar('candidato','${c.id}')">Remover</button>` : '—'}</td>
  </tr>`).join('');
}

// ── CANDIDATURAS ──
async function carregarCandidaturas() {
  const estado = document.getElementById('pesq-estado').value;
  const url = '/api/candidaturas' + (estado ? `?estado=${encodeURIComponent(estado)}` : '');
  const rows = await fetch(url).then(r=>r.json());
  const tbody = document.getElementById('cands-tbody');
  if (!rows.length) { tbody.innerHTML='<tr><td colspan="7" class="empty">Sem candidaturas.</td></tr>'; return; }
  const podeAlterar = papelActual === 'recrutador';
  tbody.innerHTML = rows.map(r=>`<tr>
    <td>${r.nomeCandidato||'—'}</td>
    <td>${r.tituloVaga||'—'}</td>
    <td>${r.nomeEmpresa||'—'}</td>
    <td>${r.data||'—'}</td>
    <td>${badge(r.estado)}</td>
    <td>${r.pontuacao ? barra(r.pontuacao) : '—'}</td>
    <td>${podeAlterar ? `<select onchange="alterarEstado('${r.id}',this.value)" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:4px 8px;border-radius:6px;font-size:.78rem;">
      <option value="">Alterar estado…</option>
      <option>Em análise</option><option>Aprovada</option>
      <option>Rejeitada</option><option>Em entrevista</option><option>Contratado</option>
    </select>` : badge(r.estado)}</td>
  </tr>`).join('');
}

// ── EXPORTAR CSV ──
async function exportarCSV() {
  const rows = await fetch('/api/candidaturas').then(r=>r.json());
  const cols = ['nomeCandidato','tituloVaga','nomeEmpresa','data','estado','pontuacao'];
  const header = ['Candidato','Vaga','Empresa','Data','Estado','Score'];
  const linhas = [header.join(';'), ...rows.map(r => cols.map(c => '"'+(r[c]||'')+'"').join(';'))];
  const blob = new Blob([linhas.join('\n')], {type:'text/csv;charset=utf-8;'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'candidaturas_isph.csv';
  a.click();
}

async function exportarRanking() {
  const data = await fetch('/api/analise-semantica').then(r=>r.json());
  const header = ['Candidato','Vaga','Empresa','Score','Compatível','Comp OK','Comp Total','Exp Cand','Exp Min'];
  const linhas = [header.join(';'), ...data.matches.map(m=>[
    `"${m.candidato}"`,`"${m.vaga}"`,`"${m.empresa}"`,
    m.score, m.compativel?'Sim':'Não',
    m.competenciasOk, m.competenciasTotal, m.anosCandidato, m.anosMinimos
  ].join(';'))];
  const blob = new Blob([linhas.join('\n')], {type:'text/csv;charset=utf-8;'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'ranking_semantico_isph.csv';
  a.click();
}

// ── PROCESSO DE SELECÇÃO ──
async function carregarSelecao() {
  const vagaFiltro  = document.getElementById('sel-vaga-filtro').value;
  const etapaFiltro = document.getElementById('sel-etapa-filtro').value;
  let url = '/api/selecao';
  const params = [];
  if (vagaFiltro)  params.push('vaga='  + encodeURIComponent(vagaFiltro));
  if (etapaFiltro) params.push('etapa=' + encodeURIComponent(etapaFiltro));
  if (params.length) url += '?' + params.join('&');

  const data = await fetch(url).then(r=>r.json());

  // Popula select de vagas
  const sel = document.getElementById('sel-vaga-filtro');
  if (sel.options.length <= 1 && data.vagas && data.vagas.length) {
    data.vagas.forEach(v => {
      const o = document.createElement('option');
      o.value = v.id; o.textContent = v.titulo;
      sel.appendChild(o);
    });
    sel.value = vagaFiltro;
  }

  const tbody = document.getElementById('sel-tbody');
  const etapas = ['Triagem','Entrevista RH','Entrevista Técnica','Proposta','Contratado','Rejeitado'];
  const podeAvancar = papelActual === 'recrutador';

  if (!data.candidaturas.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">Sem candidaturas neste filtro.</td></tr>';
    return;
  }

  tbody.innerHTML = data.candidaturas.map(c => {
    const etapaActual = c.etapa || 'Triagem';
    const idxActual   = etapas.indexOf(etapaActual);
    const proximas    = etapas.filter((e, i) => i > idxActual);
    const optsAvanco  = proximas.map(e => `<option value="${e}">${e}</option>`).join('');

    return `<tr>
      <td><strong>${c.nomeCandidato}</strong></td>
      <td>${c.tituloVaga}</td>
      <td>${badge(etapaActual)}</td>
      <td>${c.pontuacao ? barra(c.pontuacao) : '—'}</td>
      <td>${podeAvancar && optsAvanco ? `
        <select onchange="avancarEtapa('${c.id}',this.value,this)" style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:4px 8px;border-radius:6px;font-size:.78rem;">
          <option value="">Avançar para…</option>
          ${optsAvanco}
        </select>` : (etapaActual==='Contratado'||etapaActual==='Rejeitado' ? badge(etapaActual) : '—')}
      </td>
      <td>
        <button class="btn-sm" onclick="verHistorico('${c.id}','${(c.nomeCandidato||'').replace(/'/g,"\\'")}')">Ver</button>
      </td>
    </tr>`;
  }).join('');
}

async function avancarEtapa(candId, etapa, sel) {
  if (!etapa) return;
  sel.value = '';
  const r = await fetch(`/api/selecao/${candId}`, {
    method: 'PATCH',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({etapa})
  });
  const d = await r.json();
  if (d.ok) { toast(`Avançado para: ${etapa}`); carregarSelecao(); }
  else toast('Erro: ' + d.erro, 'var(--red)');
}

async function verHistorico(candId, nome) {
  const data = await fetch(`/api/selecao/${candId}/historico`).then(r=>r.json());
  document.getElementById('hist-cand-nome').textContent = nome;
  const tl = document.getElementById('hist-timeline');
  tl.innerHTML = data.historico.length
    ? data.historico.map(h => {
        const cls = h.etapa === 'Rejeitado' ? 'rejected' : h.etapa === 'Contratado' ? 'done' : '';
        return `<div class="etapa-item">
          <div class="etapa-dot ${cls}"></div>
          <span>${badge(h.etapa)}</span>
          <span style="color:var(--muted);font-size:.78rem;margin-left:6px;">${h.data}</span>
          ${h.nota ? `<span style="color:var(--text);margin-left:6px;">${h.nota}</span>` : ''}
        </div>`;
      }).join('')
    : '<p style="color:var(--muted)">Sem histórico registado.</p>';
  document.getElementById('modal-hist').classList.add('open');
}
function fecharModalHist() { document.getElementById('modal-hist').classList.remove('open'); }

// ── ANÁLISE SEMÂNTICA ──
async function carregarAnalise() {
  const vaga = document.getElementById('analise-vaga').value;
  const url = '/api/analise-semantica' + (vaga ? `?vaga=${encodeURIComponent(vaga)}` : '');
  const data = await fetch(url).then(r=>r.json());

  const sel = document.getElementById('analise-vaga');
  if (sel.options.length <= 1 && data.vagas.length) {
    sel.innerHTML = '<option value="">Todas as vagas</option>' +
      data.vagas.map(v=>`<option value="${v.id}">${v.titulo}</option>`).join('');
    sel.value = vaga;
  }

  const matches = data.matches || [];
  document.getElementById('match-grid').innerHTML = matches.length
    ? matches.map(m=>`
      <div class="semantic-card">
        <h4>${m.candidato}</h4>
        <div class="meta">📋 ${m.vaga}<br>🏢 ${m.empresa}</div>
        <div class="score">${Math.round(m.score * 100)}%</div>
        ${badge(m.compativel ? 'Compatível' : 'Com gaps')}
        <div class="semantic-list">
          <span class="badge b-blue">⚙️ ${m.competenciasOk}/${m.competenciasTotal} comp.</span>
          <span class="badge ${m.experienciaOk ? 'b-green' : 'b-yellow'}">⏱ ${m.anosCandidato}/${m.anosMinimos} anos</span>
        </div>
      </div>`).join('')
    : '<p class="empty">Sem resultados de matching.</p>';

  const gaps = data.gaps || [];
  document.getElementById('gap-tbody').innerHTML = gaps.length
    ? gaps.map(g=>`<tr>
        <td>${g.candidato}</td><td>${g.vaga}</td>
        <td>${g.competenciasFalta.length
          ? g.competenciasFalta.map(c=>`<span class="badge b-yellow">${c}</span>`).join(' ')
          : '<span class="badge b-green">Sem gap</span>'}</td>
        <td>${g.anosCandidato} / ${g.anosMinimos} anos</td>
        <td>${badge(g.compativel ? 'Compatível' : 'Com gaps')}</td>
      </tr>`).join('')
    : '<tr><td colspan="5" class="empty">Todos compatíveis.</td></tr>';

  document.getElementById('vocab-tbody').innerHTML = data.vocabularios.length
    ? data.vocabularios.map(v=>`<tr>
        <td>${v.recurso}</td><td>${v.tipo}</td>
        <td>${v.link ? `<a href="${v.link}" target="_blank" rel="noopener" style="color:var(--green);">${v.alinhamento}</a>` : v.alinhamento}</td>
      </tr>`).join('')
    : '<tr><td colspan="3" class="empty">Sem alinhamentos.</td></tr>';

  const inf = await fetch('/api/inferencia').then(r=>r.json());
  document.getElementById('inf-tbody').innerHTML = inf.factos.length
    ? inf.factos.map(f=>`<tr>
        <td><span class="badge b-blue">${f.sujeito}</span></td>
        <td style="color:var(--accent);font-size:.8rem;">${f.propriedade}</td>
        <td><span class="badge b-green">${f.objecto}</span></td>
      </tr>`).join('')
    : '<tr><td colspan="3" class="empty">Sem factos inferidos.</td></tr>';
}

// ── EMPRESAS ──
async function carregarEmpresas() {
  const rows = await fetch('/api/empresas').then(r=>r.json());
  document.getElementById('emp-tbody').innerHTML = rows.length
    ? rows.map(e=>`<tr>
        <td><strong>${e.nome||'—'}</strong></td>
        <td>${e.sector||'—'}</td>
        <td>${e.local||'—'}</td>
        <td><span class="badge b-blue">${e.totalVagas||0} vagas</span></td>
      </tr>`).join('')
    : '<tr><td colspan="4" class="empty">Sem empresas.</td></tr>';
}

// ── RELATÓRIOS ──
async function carregarRelatorios() {
  const s = await fetch('/api/stats').then(r=>r.json());
  renderFunil('funil-rel', s.funil || {});

  // Por estado
  const cands = await fetch('/api/candidaturas').then(r=>r.json());
  const total = cands.length || 1;
  const porEstado = {};
  cands.forEach(c => { porEstado[c.estado] = (porEstado[c.estado]||0)+1; });
  document.getElementById('rel-estado-tbody').innerHTML =
    Object.entries(porEstado).sort((a,b)=>b[1]-a[1]).map(([e,n])=>`<tr>
      <td>${badge(e)}</td><td><strong>${n}</strong></td>
      <td>${Math.round(n/total*100)}%</td>
    </tr>`).join('') || '<tr><td colspan="3" class="empty">Sem dados.</td></tr>';

  // Top candidatos
  const analise = await fetch('/api/analise-semantica').then(r=>r.json());
  const top = [...analise.matches].sort((a,b)=>b.score-a.score).slice(0,10);
  document.getElementById('rel-top-tbody').innerHTML = top.length
    ? top.map((m,i)=>`<tr>
        <td><strong>#${i+1}</strong></td>
        <td>${m.candidato}</td><td>${m.vaga}</td>
        <td>${barra(m.score)}</td>
        <td>${badge(m.compativel?'Compatível':'Com gaps')}</td>
      </tr>`).join('')
    : '<tr><td colspan="5" class="empty">Sem dados.</td></tr>';
}

// ── FORMULÁRIOS ──
async function carregarFormularios() {
  const comps = await fetch('/api/competencias').then(r=>r.json());
  const mkChecks = () => comps.map(c=>`
    <label><input type="checkbox" value="${c.id}" data-nome="${c.nome}"> ${c.nome}${c.esco ? ` <a href="${c.esco}" target="_blank" rel="noopener" style="color:var(--green);text-decoration:none;">ESCO</a>` : ''}</label>`).join('');
  document.getElementById('comp-check-cand').innerHTML = mkChecks();
  document.getElementById('comp-check-vaga').innerHTML = mkChecks();
  const emps = await fetch('/api/empresas').then(r=>r.json());
  document.getElementById('v-empresa').innerHTML =
    '<option value="">— Selecciona a empresa —</option>' +
    emps.map(e=>`<option value="${e.id}">${e.nome}</option>`).join('');
  document.getElementById('v-data').value = new Date().toISOString().slice(0,10);
}

async function cadastrarCandidato() {
  const nome = document.getElementById('c-nome').value.trim();
  const email = document.getElementById('c-email').value.trim();
  if (!nome || !email) { toast('Nome e email são obrigatórios.','var(--red)'); return; }
  const comps = [...document.querySelectorAll('#comp-check-cand input:checked')].map(i=>i.value);
  const body = { nome, email,
    telefone: document.getElementById('c-tel').value,
    anos: document.getElementById('c-exp').value || '0',
    grau: document.getElementById('c-grau').value,
    area: document.getElementById('c-area').value,
    instituicao: document.getElementById('c-inst').value,
    cargoExperiencia: document.getElementById('c-cargo').value,
    empresaExperiencia: document.getElementById('c-empresa-exp').value,
    competencias: comps };
  const d = await fetch('/api/candidatos',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json());
  if (d.ok) { toast('Candidato registado!'); limparForm(['c-nome','c-email','c-tel','c-exp','c-area','c-inst','c-cargo','c-empresa-exp']); }
  else toast('Erro: '+d.erro,'var(--red)');
}

async function cadastrarVaga() {
  const titulo = document.getElementById('v-titulo').value.trim();
  const empresa = document.getElementById('v-empresa').value;
  if (!titulo || !empresa) { toast('Título e empresa são obrigatórios.','var(--red)'); return; }
  const comps = [...document.querySelectorAll('#comp-check-vaga input:checked')].map(i=>i.value);
  const body = { titulo, empresa,
    local: document.getElementById('v-local').value,
    modalidade: document.getElementById('v-modal').value,
    salario: document.getElementById('v-salario').value,
    data: document.getElementById('v-data').value,
    descricao: document.getElementById('v-desc').value,
    expMinima: document.getElementById('v-exp-min').value,
    competencias: comps };
  const d = await fetch('/api/vagas',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json());
  if (d.ok) { toast('Vaga publicada!'); limparForm(['v-titulo','v-local','v-salario','v-desc','v-exp-min']); }
  else toast('Erro: '+d.erro,'var(--red)');
}

async function cadastrarEmpresa() {
  const nome = document.getElementById('e-nome').value.trim();
  if (!nome) { toast('Nome da empresa é obrigatório.','var(--red)'); return; }
  const body = { nome, sector: document.getElementById('e-sector').value, local: document.getElementById('e-local').value };
  const d = await fetch('/api/empresas',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json());
  if (d.ok) { toast('Empresa registada!'); limparForm(['e-nome','e-sector','e-local']); carregarFormularios(); }
  else toast('Erro: '+d.erro,'var(--red)');
}

async function cadastrarCompetencia() {
  const nome = document.getElementById('k-nome').value.trim();
  if (!nome) { toast('Nome da competência é obrigatório.','var(--red)'); return; }
  const body = { nome, tipo: document.getElementById('k-tipo').value,
    nivel: document.getElementById('k-nivel').value,
    categoria: document.getElementById('k-cat').value };
  const d = await fetch('/api/competencias',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json());
  if (d.ok) { toast('Competência adicionada!'); limparForm(['k-nome','k-cat']); carregarFormularios(); }
  else toast('Erro: '+d.erro,'var(--red)');
}

// ── MODAL CANDIDATURA (fluxo real) ──
let vagaJaCandidatada = false;

async function abrirModal(vagaId, vagaTitulo) {
  vagaSelecionada = vagaId;
  vagaJaCandidatada = false;
  document.getElementById('modal-vaga-titulo').textContent = vagaTitulo;
  document.getElementById('modal-carta').value = '';
  document.getElementById('modal-match-info').innerHTML = '';
  document.getElementById('modal-aviso-dup').style.display = 'none';
  document.getElementById('modal-btn-submeter').disabled = false;
  document.getElementById('modal-btn-submeter').style.opacity = '1';

  // Carrega detalhes da vaga
  const det = await fetch(`/api/vagas/${vagaId}/detalhes`).then(r=>r.json());
  document.getElementById('modal-vaga-detalhes').innerHTML = `
    <div style="font-size:.95rem;font-weight:600;color:#fff;margin-bottom:8px;">${det.titulo}</div>
    <div style="color:var(--accent);font-size:.82rem;margin-bottom:10px;">🏢 ${det.empresa}${det.sector ? ' · '+det.sector : ''}</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 16px;">
      <span>📍 ${det.local || '—'}</span>
      <span>💻 ${det.modalidade || '—'}</span>
      <span>💰 ${det.salario ? Number(det.salario).toLocaleString('pt-AO')+' AOA' : 'A definir'}</span>
      <span>⏱ Exp. mín: ${det.expMinima || '0'} anos</span>
    </div>
    ${det.descricao ? `<div style="margin-top:10px;color:var(--text);font-size:.83rem;line-height:1.6;">${det.descricao}</div>` : ''}
    ${det.competencias.length ? `<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px;">${det.competencias.map(c=>`<span class="badge b-blue">${c}</span>`).join('')}</div>` : ''}
  `;

  // Modo candidato: mostra match automático e verifica duplicado
  if (papelActual === 'candidato') {
    document.getElementById('modal-sel-cand-wrap').style.display = 'none';
    document.getElementById('modal-carta-wrap').style.display = 'block';
    if (candidatoIdActual) {
      // Verifica match
      const m = await fetch(`/api/match?vaga=${vagaId}&candidato=${candidatoIdActual}`).then(r=>r.json());
      document.getElementById('modal-match-info').innerHTML = m.compativel
        ? `<div style="background:rgba(0,212,170,.1);border:1px solid var(--green);border-radius:8px;padding:10px 14px;color:var(--green);font-size:.85rem;">✅ O teu perfil é <strong>compatível</strong> com esta vaga!</div>`
        : `<div style="background:rgba(255,193,7,.1);border:1px solid var(--yellow);border-radius:8px;padding:10px 14px;color:var(--yellow);font-size:.85rem;">⚠️ Competências em falta: <strong>${m.gap.join(', ')}</strong><br><span style="font-size:.78rem;opacity:.8">Podes candidatar-te na mesma.</span></div>`;
      // Verifica duplicado
      const minhas = await fetch('/api/minhas-candidaturas').then(r=>r.json());
      const jaExiste = minhas.some(c => {
        // compara pelo título (simples) ou podemos comparar pelo vaga id via outro campo
        return c.tituloVaga === det.titulo && c.nomeEmpresa === det.empresa;
      });
      if (jaExiste) {
        vagaJaCandidatada = true;
        document.getElementById('modal-aviso-dup').style.display = 'block';
        document.getElementById('modal-btn-submeter').disabled = true;
        document.getElementById('modal-btn-submeter').style.opacity = '.5';
      }
    } else {
      document.getElementById('modal-match-info').innerHTML =
        `<div style="color:var(--yellow);font-size:.84rem;">⚠️ A tua conta não tem perfil de candidato associado. Contacta o administrador.</div>`;
      document.getElementById('modal-btn-submeter').disabled = true;
      document.getElementById('modal-btn-submeter').style.opacity = '.5';
    }
  } else {
    // Recrutador: mostra seletor de candidato
    document.getElementById('modal-carta-wrap').style.display = 'none';
    document.getElementById('modal-sel-cand-wrap').style.display = 'block';
    const cands = await fetch('/api/candidatos').then(r=>r.json());
    const sel = document.getElementById('modal-candidato');
    sel.innerHTML = '<option value="">— Selecciona o candidato —</option>' +
      cands.map(c=>`<option value="${c.id}">${c.nome}</option>`).join('');
    sel.onchange = () => {
      if (sel.value) {
        fetch(`/api/match?vaga=${vagaId}&candidato=${sel.value}`).then(r=>r.json()).then(r=>{
          document.getElementById('modal-match-info').innerHTML = r.compativel
            ? `<p style="color:var(--green);font-size:.88rem;">✅ Perfil compatível!</p>`
            : `<p style="color:var(--yellow);font-size:.88rem;">⚠️ Faltam: <strong>${r.gap.join(', ')}</strong></p>`;
        });
      }
    };
  }

  document.getElementById('modal-cand').classList.add('open');
}

function fecharModal() { document.getElementById('modal-cand').classList.remove('open'); vagaSelecionada=null; }

async function submeterCandidatura() {
  if (vagaJaCandidatada) return;
  const carta = document.getElementById('modal-carta').value.trim();
  let body = { vaga: vagaSelecionada, cartaMotivacao: carta };
  if (papelActual !== 'candidato') {
    const candId = document.getElementById('modal-candidato').value;
    if (!candId) { toast('Selecciona o candidato.','var(--red)'); return; }
    body.candidato = candId;
  }
  const d = await fetch('/api/candidaturas',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)}).then(r=>r.json());
  if (d.ok) {
    toast('Candidatura submetida com sucesso! ✓');
    fecharModal();
    // Se candidato, recarrega as suas candidaturas
    if (papelActual === 'candidato') {
      // atualiza badge se estiver na secção
    }
  }
  else toast('Erro: '+d.erro,'var(--red)');
}

// ── AS MINHAS CANDIDATURAS ──
async function carregarMinhasCandidaturas() {
  const lista = await fetch('/api/minhas-candidaturas').then(r=>r.json());
  const el = document.getElementById('minhas-cands-lista');
  if (!lista.length) {
    el.innerHTML = `<div class="empty" style="padding:60px;">
      <div style="font-size:2rem;margin-bottom:12px;">📋</div>
      <p>Ainda não submeteste nenhuma candidatura.</p>
      <p style="font-size:.82rem;margin-top:8px;">Vai a <strong>Vagas</strong> e clica em "Candidatar-me".</p>
    </div>`;
    return;
  }
  const estadoCls = {
    'Em análise':'b-yellow','Aprovada':'b-green','Rejeitada':'b-red',
    'Em entrevista':'b-blue','Contratado':'b-green',
  };
  el.innerHTML = lista.map(c => {
    const pct = c.pontuacao ? Math.round(parseFloat(c.pontuacao)*100) : null;
    const hist = c.historico.map(h => `
      <div class="etapa-item">
        <div class="etapa-dot ${h.etapa==='Contratado'?'done':h.etapa==='Rejeitado'?'rejected':''}"></div>
        <span>${badge(h.etapa)}</span>
        <span style="color:var(--muted);font-size:.76rem;margin-left:6px;">${h.data}</span>
        ${h.nota ? `<span style="color:var(--text);font-size:.78rem;margin-left:6px;">${h.nota}</span>` : ''}
      </div>`).join('');
    return `
    <div class="form-card" style="margin-bottom:16px;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:10px;">
        <div>
          <div style="font-size:1.05rem;font-weight:700;color:#fff;">${c.tituloVaga || '—'}</div>
          <div style="color:var(--accent);font-size:.83rem;margin-top:3px;">🏢 ${c.nomeEmpresa || '—'}</div>
          <div style="color:var(--muted);font-size:.8rem;margin-top:3px;">
            📍 ${c.local||'—'} &nbsp;·&nbsp; 💻 ${c.modalidade||'—'} &nbsp;·&nbsp; 📅 ${c.data||'—'}
          </div>
        </div>
        <div style="text-align:right;">
          <div>${badge(c.estado)}</div>
          ${pct !== null ? `<div style="margin-top:6px;font-size:.78rem;color:var(--muted);">Compatibilidade: <strong style="color:var(--accent);">${pct}%</strong></div>` : ''}
        </div>
      </div>
      ${c.carta ? `<div style="margin-top:12px;background:var(--bg);border-left:3px solid var(--accent);padding:8px 14px;border-radius:0 6px 6px 0;font-size:.82rem;color:var(--muted);line-height:1.6;"><em>${c.carta.length>200?c.carta.slice(0,200)+'…':c.carta}</em></div>` : ''}
      ${hist ? `<div style="margin-top:14px;"><div style="font-size:.78rem;color:var(--muted);margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em;">Progresso</div><div class="etapas-timeline">${hist}</div></div>` : ''}
    </div>`;
  }).join('');
}

async function alterarEstado(candId, estado) {
  if (!estado) return;
  const d = await fetch(`/api/candidaturas/${candId}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({estado})}).then(r=>r.json());
  if (d.ok) { toast('Estado actualizado!'); carregarCandidaturas(); }
  else toast('Erro: '+d.erro,'var(--red)');
}

async function eliminar(tipo, id) {
  if (!confirm('Confirmas a remoção?')) return;
  const d = await fetch(`/api/${tipo}s/${id}`,{method:'DELETE'}).then(r=>r.json());
  if (d.ok) { toast('Removido!'); if(tipo==='candidato') carregarCandidatos(); }
  else toast('Erro: '+d.erro,'var(--red)');
}

function limparForm(ids) { ids.forEach(id => { const el=document.getElementById(id); if(el) el.value=''; }); }

document.getElementById('modal-cand').addEventListener('click', function(e){ if(e.target===this) fecharModal(); });
document.getElementById('modal-hist').addEventListener('click', function(e){ if(e.target===this) fecharModalHist(); });
</script>
</body>
</html>
"""

# ============================================================
#  ROTAS DE AUTENTICAÇÃO
# ============================================================

# Rota pública para carregar competências no formulário de registo
@app.route("/api/competencias-pub")
def api_competencias_pub():
    rows = g.query(PREFIXOS + """
        SELECT ?id ?nome WHERE {
            { ?id rdf:type rec:Competencia }
            UNION { ?id rdf:type rec:CompetenciaTecnica }
            UNION { ?id rdf:type rec:CompetenciaComportamental }
            ?id rec:nomeCompetencia ?nome .
        } ORDER BY ?nome
    """)
    out = []
    seen = set()
    for r in rows:
        cid = str(r.id).split("#")[-1]
        if cid not in seen:
            seen.add(cid)
            out.append({"id": cid, "nome": str(r.nome)})
    return jsonify(out)

@app.route("/registar", methods=["POST"])
def registar():
    d     = request.get_json()
    user  = (d.get("user") or "").strip().lower()
    senha = (d.get("senha") or "").strip()
    nome  = (d.get("nome")  or "").strip()
    email = (d.get("email") or "").strip()

    if not user or not senha or not nome or not email:
        return jsonify({"ok": False, "erro": "Campos obrigatórios em falta."}), 400
    if len(senha) < 4:
        return jsonify({"ok": False, "erro": "Palavra-passe demasiado curta (mín. 4 caracteres)."}), 400
    if user in USERS:
        return jsonify({"ok": False, "erro": f"O utilizador '{user}' já existe. Escolhe outro nome."}), 400

    # Cria nó RDF do candidato
    safe = "".join(c for c in nome.title() if c.isalnum())
    uid  = "Candidato_" + safe + "_" + uuid.uuid4().hex[:6]
    node = REC[uid]
    g.add((node, RDF.type,         REC.Candidato))
    g.add((node, RDF.type,         FOAF_NS.Person))
    g.add((node, REC.nomeCompleto, Literal(nome)))
    g.add((node, FOAF_NS.name,     Literal(nome)))
    g.add((node, REC.email,        Literal(email)))
    g.add((node, FOAF_NS.mbox,     URIRef("mailto:" + email)))
    if d.get("telefone"): g.add((node, REC.telefone,       Literal(d["telefone"])))
    if d.get("anos"):     g.add((node, REC.anoExperiencia, Literal(int(d["anos"]), datatype=XSD.integer)))
    if d.get("grau") or d.get("area") or d.get("instituicao"):
        fuid  = "Form_" + uuid.uuid4().hex[:8]
        fnode = REC[fuid]
        g.add((fnode, RDF.type, REC.FormacaoAcademica))
        if d.get("grau"):        g.add((fnode, REC.grauFormacao, Literal(d["grau"])))
        if d.get("area"):        g.add((fnode, REC.areaFormacao, Literal(d["area"])))
        if d.get("instituicao"): g.add((fnode, REC.instituicao,  Literal(d["instituicao"])))
        g.add((node, REC.possuiFormacao, fnode))
    if d.get("cargoExperiencia") or d.get("anos"):
        exp_uid  = "Exp_" + uuid.uuid4().hex[:8]
        exp_node = REC[exp_uid]
        g.add((exp_node, RDF.type, REC.ExperienciaProfissional))
        if d.get("cargoExperiencia"): g.add((exp_node, REC.cargoExperiencia, Literal(d["cargoExperiencia"])))
        if d.get("anos"):             g.add((exp_node, REC.anosExperiencia,  Literal(float(d["anos"]), datatype=XSD.decimal)))
        g.add((node, REC.possuiExperiencia, exp_node))
    for comp_id in d.get("competencias", []):
        g.add((node, REC.possuiCompetencia, REC[comp_id]))
    salvar()

    # Regista utilizador em memória e faz login
    USERS[user] = {
        "senha":        hashlib.sha256(senha.encode()).hexdigest(),
        "papel":        "candidato",
        "nome":         nome,
        "candidato_id": uid,
    }
    session["user"]         = user
    session["papel"]        = "candidato"
    session["nome"]         = nome
    session["candidato_id"] = uid
    print(f"  [Registo] Novo candidato: {user} → {uid}")
    return jsonify({"ok": True, "id": uid})
@app.route("/login", methods=["GET"])
def login_page():
    if "user" in session:
        return redirect(url_for("index"))
    return render_template_string(HTML_LOGIN, erro=None)

@app.route("/login", methods=["POST"])
def login_post():
    user  = request.form.get("user","").strip().lower()
    senha = request.form.get("senha","")
    h     = hashlib.sha256(senha.encode()).hexdigest()
    if user in USERS and USERS[user]["senha"] == h:
        session["user"]         = user
        session["papel"]        = USERS[user]["papel"]
        session["nome"]         = USERS[user]["nome"]
        session["candidato_id"] = USERS[user].get("candidato_id")
        return redirect(url_for("index"))
    return render_template_string(HTML_LOGIN, erro="Utilizador ou palavra-passe incorrectos.")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

@app.route("/api/me")
@login_required
def api_me():
    return jsonify({
        "user":        session["user"],
        "papel":       session["papel"],
        "nome":        session["nome"],
        "candidatoId": session.get("candidato_id"),
    })

# ============================================================
#  ROTA PRINCIPAL
# ============================================================
@app.route("/")
@login_required
def index():
    return render_template_string(HTML)

# ============================================================
#  API — STATS (com funil)
# ============================================================
@app.route("/api/stats")
@login_required
def api_stats():
    def count(cls):
        return len(list(g.subjects(RDF.type, REC[cls])))

    # Funil: conta candidaturas por etapa
    etapas_order = ["Triagem","Entrevista RH","Entrevista Técnica","Proposta","Contratado","Rejeitado"]
    funil = {e: 0 for e in etapas_order}
    for cand in g.subjects(RDF.type, REC.Candidatura):
        etapa = str(g.value(cand, REC.etapaSeleção) or "Triagem")
        if etapa in funil:
            funil[etapa] += 1

    return jsonify({
        "candidatos":   count("Candidato"),
        "vagas":        count("Vaga"),
        "empresas":     count("Empresa"),
        "competencias": count("Competencia") + count("CompetenciaTecnica") + count("CompetenciaComportamental"),
        "candidaturas": count("Candidatura"),
        "funil": funil,
    })

# ============================================================
#  API — COMPETÊNCIAS
# ============================================================
@app.route("/api/competencias", methods=["GET"])
@login_required
def api_get_competencias():
    rows = g.query(PREFIXOS + """
        SELECT ?id ?nome ?nivel ?categoria ?esco
        WHERE {
            { ?id rdf:type rec:Competencia } UNION
            { ?id rdf:type rec:CompetenciaTecnica } UNION
            { ?id rdf:type rec:CompetenciaComportamental }
            ?id rec:nomeCompetencia ?nome .
            OPTIONAL { ?id rec:nivelCompetencia ?nivel }
            OPTIONAL { ?id rec:categoriaCompetencia ?categoria }
            OPTIONAL { ?id rec:referenciaESCO ?esco }
        } ORDER BY ?nome
    """)
    out = []
    for r in rows:
        out.append({
            "id":        str(r.id).split("#")[-1],
            "nome":      str(r.nome)      if r.nome      else "",
            "nivel":     str(r.nivel)     if r.nivel     else "",
            "categoria": str(r.categoria) if r.categoria else "",
            "esco":      str(r.esco)      if r.esco      else "",
        })
    return jsonify(out)

@app.route("/api/competencias", methods=["POST"])
@login_required
@recrutador_required
def api_post_competencia():
    d    = request.get_json()
    nome = d.get("nome","").strip()
    if not nome:
        return jsonify({"ok": False, "erro": "Nome obrigatório"}), 400
    uid  = "Comp_" + uuid.uuid4().hex[:8]
    tipo = d.get("tipo", "CompetenciaTecnica")
    node = REC[uid]
    g.add((node, RDF.type, REC[tipo]))
    g.add((node, REC.nomeCompetencia, Literal(nome)))
    if d.get("nivel"):     g.add((node, REC.nivelCompetencia,    Literal(d["nivel"])))
    if d.get("categoria"): g.add((node, REC.categoriaCompetencia, Literal(d["categoria"])))
    if d.get("esco"):      g.add((node, REC.referenciaESCO,      Literal(d["esco"], datatype=XSD.anyURI)))
    salvar()
    return jsonify({"ok": True, "id": uid})

# ============================================================
#  API — CANDIDATOS
# ============================================================
@app.route("/api/candidatos", methods=["GET"])
@login_required
def api_get_candidatos():
    rows = g.query(PREFIXOS + """
        SELECT ?id ?nome ?email ?anos ?grau ?area ?inst
        WHERE {
            ?id rdf:type rec:Candidato .
            ?id rec:nomeCompleto ?nome .
            OPTIONAL { ?id rec:email ?email }
            OPTIONAL { ?id rec:anoExperiencia ?anos }
            OPTIONAL {
                ?id rec:possuiFormacao ?f .
                ?f rec:grauFormacao ?grau .
                ?f rec:areaFormacao ?area .
                ?f rec:instituicao ?inst .
            }
        } ORDER BY ?nome
    """)
    cands = {}
    for r in rows:
        cid = str(r.id).split("#")[-1]
        if cid not in cands:
            cands[cid] = {
                "id":    cid,
                "nome":  str(r.nome)  if r.nome  else "",
                "email": str(r.email) if r.email else "",
                "anos":  str(r.anos)  if r.anos  else "0",
                "grau":  str(r.grau)  if r.grau  else "",
                "area":  str(r.area)  if r.area  else "",
                "competencias": ""
            }
    comp_rows = g.query(PREFIXOS + """
        SELECT ?id ?nomeComp WHERE {
            ?id rdf:type rec:Candidato .
            ?id rec:possuiCompetencia ?c .
            ?c rec:nomeCompetencia ?nomeComp .
        }
    """)
    for r in comp_rows:
        cid = str(r.id).split("#")[-1]
        if cid in cands:
            existing = cands[cid]["competencias"]
            cands[cid]["competencias"] = (existing + ", " + str(r.nomeComp)).strip(", ")
    return jsonify(list(cands.values()))

@app.route("/api/candidatos", methods=["POST"])
@login_required
@recrutador_required
def api_post_candidato():
    d     = request.get_json()
    nome  = d.get("nome","").strip()
    email = d.get("email","").strip()
    if not nome or not email:
        return jsonify({"ok": False, "erro": "Nome e email obrigatórios"}), 400
    uid  = "Candidato_" + uuid.uuid4().hex[:8]
    node = REC[uid]
    g.add((node, RDF.type, REC.Candidato))
    g.add((node, RDF.type, FOAF_NS.Person))
    g.add((node, REC.nomeCompleto, Literal(nome)))
    g.add((node, FOAF_NS.name,     Literal(nome)))
    g.add((node, REC.email,        Literal(email)))
    g.add((node, FOAF_NS.mbox,     URIRef("mailto:" + email)))
    if d.get("telefone"): g.add((node, REC.telefone,       Literal(d["telefone"])))
    if d.get("anos"):     g.add((node, REC.anoExperiencia, Literal(int(d["anos"]), datatype=XSD.integer)))
    if d.get("grau") or d.get("area"):
        fuid  = "Form_" + uuid.uuid4().hex[:8]
        fnode = REC[fuid]
        g.add((fnode, RDF.type, REC.FormacaoAcademica))
        if d.get("grau"):        g.add((fnode, REC.grauFormacao,  Literal(d["grau"])))
        if d.get("area"):        g.add((fnode, REC.areaFormacao,  Literal(d["area"])))
        if d.get("instituicao"): g.add((fnode, REC.instituicao,   Literal(d["instituicao"])))
        g.add((node, REC.possuiFormacao, fnode))
    if d.get("cargoExperiencia") or d.get("empresaExperiencia") or d.get("anos"):
        exp_uid  = "Exp_" + uuid.uuid4().hex[:8]
        exp_node = REC[exp_uid]
        g.add((exp_node, RDF.type, REC.ExperienciaProfissional))
        if d.get("cargoExperiencia"):   g.add((exp_node, REC.cargoExperiencia,   Literal(d["cargoExperiencia"])))
        if d.get("empresaExperiencia"): g.add((exp_node, REC.empresaExperiencia, Literal(d["empresaExperiencia"])))
        if d.get("anos"):               g.add((exp_node, REC.anosExperiencia,    Literal(float(d["anos"]), datatype=XSD.decimal)))
        g.add((node, REC.possuiExperiencia, exp_node))
    for comp_id in d.get("competencias", []):
        g.add((node, REC.possuiCompetencia, REC[comp_id]))
    salvar()
    return jsonify({"ok": True, "id": uid})

@app.route("/api/candidatos/<cid>", methods=["DELETE"])
@login_required
@recrutador_required
def api_del_candidato(cid):
    node = REC[cid]
    g.remove((node, None, None))
    g.remove((None, None, node))
    salvar()
    return jsonify({"ok": True})

# ============================================================
#  API — VAGAS
# ============================================================
@app.route("/api/vagas", methods=["GET"])
@login_required
def api_get_vagas():
    rows = g.query(PREFIXOS + """
        SELECT ?id ?titulo ?empresa ?nomeEmp ?local ?modalidade ?salario ?data
        WHERE {
            ?id rdf:type rec:Vaga .
            ?id rec:tituloVaga ?titulo .
            OPTIONAL { ?id rec:localVaga ?local }
            OPTIONAL { ?id rec:modalidade ?modalidade }
            OPTIONAL { ?id rec:salario ?salario }
            OPTIONAL { ?id rec:dataPublicacao ?data }
            OPTIONAL {
                ?id rec:isPublicadaPor ?empresa .
                ?empresa rec:nomeEmpresa ?nomeEmp .
            }
        } ORDER BY ?titulo
    """)
    vagas = {}
    for r in rows:
        vid = str(r.id).split("#")[-1]
        if vid not in vagas:
            vagas[vid] = {
                "id":         vid,
                "titulo":     str(r.titulo)     if r.titulo     else "",
                "empresa":    str(r.nomeEmp)    if r.nomeEmp    else "",
                "local":      str(r.local)      if r.local      else "",
                "modalidade": str(r.modalidade) if r.modalidade else "",
                "salario":    str(r.salario)    if r.salario    else "",
                "data":       str(r.data)       if r.data       else "",
                "competencias": ""
            }
    cr = g.query(PREFIXOS + """
        SELECT ?id ?nomeComp WHERE {
            ?id rdf:type rec:Vaga .
            ?id rec:requerCompetencia ?c .
            ?c rec:nomeCompetencia ?nomeComp .
        }
    """)
    for r in cr:
        vid = str(r.id).split("#")[-1]
        if vid in vagas:
            existing = vagas[vid]["competencias"]
            vagas[vid]["competencias"] = (existing + ", " + str(r.nomeComp)).strip(", ")
    return jsonify(list(vagas.values()))

@app.route("/api/vagas", methods=["POST"])
@login_required
@recrutador_required
def api_post_vaga():
    d      = request.get_json()
    titulo = d.get("titulo","").strip()
    emp_id = d.get("empresa","").strip()
    if not titulo or not emp_id:
        return jsonify({"ok": False, "erro": "Título e empresa obrigatórios"}), 400
    uid  = "Vaga_" + uuid.uuid4().hex[:8]
    node = REC[uid]
    g.add((node, RDF.type, REC.Vaga))
    g.add((node, REC.tituloVaga,    Literal(titulo)))
    g.add((node, REC.isPublicadaPor, REC[emp_id]))
    g.add((REC[emp_id], REC.publica, node))
    if d.get("local"):      g.add((node, REC.localVaga,      Literal(d["local"])))
    if d.get("modalidade"): g.add((node, REC.modalidade,     Literal(d["modalidade"])))
    if d.get("salario"):    g.add((node, REC.salario,        Literal(float(d["salario"]), datatype=XSD.decimal)))
    if d.get("data"):       g.add((node, REC.dataPublicacao, Literal(d["data"], datatype=XSD.date)))
    if d.get("descricao"):  g.add((node, REC.descricaoVaga,  Literal(d["descricao"])))
    if d.get("expMinima"):
        exp_uid  = "ExpMin_" + uuid.uuid4().hex[:8]
        exp_node = REC[exp_uid]
        g.add((exp_node, RDF.type, REC.ExperienciaProfissional))
        g.add((exp_node, REC.anosExperiencia, Literal(float(d["expMinima"]), datatype=XSD.decimal)))
        g.add((node, REC.requerExperienciaMinima, exp_node))
    for comp_id in d.get("competencias", []):
        g.add((node, REC.requerCompetencia, REC[comp_id]))
    salvar()
    return jsonify({"ok": True, "id": uid})

# ============================================================
#  API — EMPRESAS
# ============================================================
@app.route("/api/empresas", methods=["GET"])
@login_required
def api_get_empresas():
    rows = g.query(PREFIXOS + """
        SELECT ?id ?nome ?sector ?local (COUNT(?v) AS ?totalVagas)
        WHERE {
            ?id rdf:type rec:Empresa .
            ?id rec:nomeEmpresa ?nome .
            OPTIONAL { ?id rec:sector ?sector }
            OPTIONAL { ?id rec:localizacaoEmpresa ?local }
            OPTIONAL { ?id rec:publica ?v }
        } GROUP BY ?id ?nome ?sector ?local ORDER BY ?nome
    """)
    out = []
    for r in rows:
        out.append({
            "id":         str(r.id).split("#")[-1],
            "nome":       str(r.nome)       if r.nome       else "",
            "sector":     str(r.sector)     if r.sector     else "",
            "local":      str(r.local)      if r.local      else "",
            "totalVagas": str(r.totalVagas) if r.totalVagas else "0",
        })
    return jsonify(out)

@app.route("/api/empresas", methods=["POST"])
@login_required
@recrutador_required
def api_post_empresa():
    d    = request.get_json()
    nome = d.get("nome","").strip()
    if not nome:
        return jsonify({"ok": False, "erro": "Nome obrigatório"}), 400
    uid  = "Empresa_" + uuid.uuid4().hex[:8]
    node = REC[uid]
    g.add((node, RDF.type, REC.Empresa))
    g.add((node, REC.nomeEmpresa, Literal(nome)))
    if d.get("sector"): g.add((node, REC.sector,             Literal(d["sector"])))
    if d.get("local"):  g.add((node, REC.localizacaoEmpresa, Literal(d["local"])))
    salvar()
    return jsonify({"ok": True, "id": uid})

# ============================================================
#  API — CANDIDATURAS
# ============================================================
@app.route("/api/candidaturas", methods=["GET"])
@login_required
def api_get_candidaturas():
    estado_filtro = request.args.get("estado", "")
    out = []
    for candidatura in g.subjects(RDF.type, REC.Candidatura):
        cand       = g.value(candidatura, REC.isSubmetidaPor)
        vaga       = g.value(candidatura, REC.refereVaga)
        estado_lit = g.value(candidatura, REC.estadoCandidatura)
        estado     = str(estado_lit) if estado_lit else "Em análise"
        if estado_filtro and estado != estado_filtro:
            continue
        emp  = g.value(vaga, REC.isPublicadaPor) if vaga else None
        data = g.value(candidatura, REC.dataCandidatura)
        out.append({
            "id":            str(candidatura).split("#")[-1],
            "nomeCandidato": str(g.value(cand, REC.nomeCompleto) or ""),
            "tituloVaga":    str(g.value(vaga, REC.tituloVaga)   or ""),
            "nomeEmpresa":   str(g.value(emp,  REC.nomeEmpresa)  or "") if emp else "",
            "data":          str(data) if data else "",
            "estado":        estado,
            "pontuacao":     str(g.value(candidatura, REC.pontuacaoMatch) or ""),
        })
    out.sort(key=lambda x: x["data"], reverse=True)
    return jsonify(out)

@app.route("/api/candidaturas", methods=["POST"])
@login_required
def api_post_candidatura():
    d       = request.get_json()
    vaga_id = d.get("vaga","").strip()
    carta   = d.get("cartaMotivacao","").strip()

    # Se o utilizador for candidato, usa o candidato ligado à sessão
    if session.get("papel") == "candidato":
        cand_id = session.get("candidato_id","").strip() if session.get("candidato_id") else ""
        if not cand_id:
            return jsonify({"ok": False, "erro": "O teu perfil de candidato não está configurado. Pede ao administrador para associar a tua conta."}), 400
    else:
        # Recrutador pode submeter em nome de qualquer candidato (via body)
        cand_id = d.get("candidato","").strip()

    if not cand_id or not vaga_id:
        return jsonify({"ok": False, "erro": "Candidato e vaga obrigatórios"}), 400

    # Verifica se já existe candidatura deste candidato para esta vaga
    for cand_node in g.objects(REC[cand_id], REC.submeteCandidatura):
        vaga_existente = g.value(cand_node, REC.refereVaga)
        if vaga_existente and str(vaga_existente).split("#")[-1] == vaga_id:
            return jsonify({"ok": False, "erro": "Já tens uma candidatura submetida para esta vaga."}), 400

    vaga_comps = set(str(o).split("#")[-1] for o in g.objects(REC[vaga_id], REC.requerCompetencia))
    cand_comps = set(str(o).split("#")[-1] for o in g.objects(REC[cand_id], REC.possuiCompetencia))
    score      = round(len(vaga_comps & cand_comps) / len(vaga_comps), 2) if vaga_comps else 1.0
    uid  = "Cand_" + uuid.uuid4().hex[:8]
    node = REC[uid]
    g.add((node, RDF.type,                REC.Candidatura))
    g.add((node, REC.isSubmetidaPor,      REC[cand_id]))
    g.add((node, REC.refereVaga,          REC[vaga_id]))
    g.add((node, REC.estadoCandidatura,   Literal("Em análise")))
    g.add((node, REC.etapaSeleção,        Literal("Triagem")))
    g.add((node, REC.dataCandidatura,     Literal(str(date.today()), datatype=XSD.date)))
    g.add((node, REC.pontuacaoMatch,      Literal(score, datatype=XSD.decimal)))
    if carta:
        g.add((node, REC.cartaMotivacao,  Literal(carta)))
    g.add((REC[cand_id], REC.submeteCandidatura, node))
    # regista primeiro evento no histórico
    _add_historico(node, "Triagem", "Candidatura submetida")
    salvar()
    return jsonify({"ok": True, "id": uid, "score": score})

@app.route("/api/candidaturas/<cid>", methods=["PATCH"])
@login_required
@recrutador_required
def api_patch_candidatura(cid):
    d      = request.get_json()
    estado = d.get("estado","")
    node   = REC[cid]
    g.remove((node, REC.estadoCandidatura, None))
    g.add((node, REC.estadoCandidatura, Literal(estado)))
    salvar()
    notificar_estado(node)
    return jsonify({"ok": True})

# ============================================================
#  API — PROCESSO DE SELECÇÃO
# ============================================================
def _add_historico(cand_node, etapa, nota=""):
    """Adiciona um evento ao histórico de selecção de uma candidatura."""
    h_uid  = "Hist_" + uuid.uuid4().hex[:8]
    h_node = REC[h_uid]
    g.add((h_node, RDF.type,          REC.HistoricoSelecao))
    g.add((h_node, REC.etapaHistorico, Literal(etapa)))
    g.add((h_node, REC.dataHistorico,  Literal(str(datetime.now().strftime("%Y-%m-%d %H:%M"))))  )
    if nota: g.add((h_node, REC.notaHistorico, Literal(nota)))
    g.add((cand_node, REC.temHistorico, h_node))

@app.route("/api/selecao")
@login_required
def api_get_selecao():
    vaga_filtro  = request.args.get("vaga","")
    etapa_filtro = request.args.get("etapa","")
    todas_vagas  = list(g.subjects(RDF.type, REC.Vaga))
    vagas_info   = [{"id": str(v).split("#")[-1],
                     "titulo": str(g.value(v, REC.tituloVaga) or "")} for v in todas_vagas]
    out = []
    for candidatura in g.subjects(RDF.type, REC.Candidatura):
        cand   = g.value(candidatura, REC.isSubmetidaPor)
        vaga   = g.value(candidatura, REC.refereVaga)
        vid    = str(vaga).split("#")[-1] if vaga else ""
        if vaga_filtro and vid != vaga_filtro:
            continue
        etapa = str(g.value(candidatura, REC.etapaSeleção) or "Triagem")
        if etapa_filtro and etapa != etapa_filtro:
            continue
        out.append({
            "id":            str(candidatura).split("#")[-1],
            "nomeCandidato": str(g.value(cand, REC.nomeCompleto) or ""),
            "tituloVaga":    str(g.value(vaga, REC.tituloVaga)   or ""),
            "etapa":         etapa,
            "pontuacao":     str(g.value(candidatura, REC.pontuacaoMatch) or ""),
        })
    out.sort(key=lambda x: (x["tituloVaga"], x["nomeCandidato"]))
    return jsonify({"vagas": vagas_info, "candidaturas": out})

@app.route("/api/selecao/<cid>", methods=["PATCH"])
@login_required
@recrutador_required
def api_patch_selecao(cid):
    d     = request.get_json()
    etapa = d.get("etapa","").strip()
    nota  = d.get("nota","").strip()
    if not etapa:
        return jsonify({"ok": False, "erro": "Etapa obrigatória"}), 400
    node = REC[cid]
    g.remove((node, REC.etapaSeleção, None))
    g.add((node, REC.etapaSeleção, Literal(etapa)))
    # sincroniza estado da candidatura
    estado_map = {
        "Triagem": "Em análise", "Entrevista RH": "Em entrevista",
        "Entrevista Técnica": "Em entrevista", "Proposta": "Aprovada",
        "Contratado": "Contratado", "Rejeitado": "Rejeitada"
    }
    novo_estado = estado_map.get(etapa, "Em análise")
    g.remove((node, REC.estadoCandidatura, None))
    g.add((node, REC.estadoCandidatura, Literal(novo_estado)))
    _add_historico(node, etapa, nota)
    salvar()
    notificar_estado(node)
    return jsonify({"ok": True})

@app.route("/api/selecao/<cid>/historico")
@login_required
def api_historico_selecao(cid):
    node = REC[cid]
    hist = []
    for h in g.objects(node, REC.temHistorico):
        hist.append({
            "etapa": str(g.value(h, REC.etapaHistorico) or ""),
            "data":  str(g.value(h, REC.dataHistorico)  or ""),
            "nota":  str(g.value(h, REC.notaHistorico)  or ""),
        })
    hist.sort(key=lambda x: x["data"])
    return jsonify({"historico": hist})

# ============================================================
#  API — ANÁLISE SEMÂNTICA
# ============================================================
def _literal(node, prop, default=""):
    val = g.value(node, prop) if node else None
    return str(val) if val else default

def _local_id(uri):
    return str(uri).split("#")[-1] if uri else ""

def _anos_cand(cand):
    anos = []
    for exp in g.objects(cand, REC.possuiExperiencia):
        val = g.value(exp, REC.anosExperiencia)
        if val: anos.append(float(val))
    direto = g.value(cand, REC.anoExperiencia)
    if direto: anos.append(float(direto))
    return max(anos) if anos else 0

def _anos_vaga(vaga):
    anos = []
    for exp in g.objects(vaga, REC.requerExperienciaMinima):
        val = g.value(exp, REC.anosExperiencia)
        if val: anos.append(float(val))
    return max(anos) if anos else 0

@app.route("/api/analise-semantica")
@login_required
def api_analise_semantica():
    vaga_filtro = request.args.get("vaga","")
    todas_vagas = list(g.subjects(RDF.type, REC.Vaga))
    candidatos  = list(g.subjects(RDF.type, REC.Candidato))
    vagas_info  = [{"id": _local_id(v), "titulo": _literal(v, REC.tituloVaga)} for v in todas_vagas]
    vagas       = [REC[vaga_filtro]] if vaga_filtro else todas_vagas

    matches, gaps = [], []
    for vaga in vagas:
        req_comps = set(g.objects(vaga, REC.requerCompetencia))
        anos_min  = _anos_vaga(vaga)
        emp       = g.value(vaga, REC.isPublicadaPor)
        for cand in candidatos:
            cand_comps = set(g.objects(cand, REC.possuiCompetencia))
            falta      = sorted(_literal(c, REC.nomeCompetencia, _local_id(c)) for c in (req_comps - cand_comps))
            ok_count   = len(req_comps & cand_comps)
            anos_cand  = _anos_cand(cand)
            exp_ok     = anos_cand >= anos_min
            comp_score = ok_count / len(req_comps) if req_comps else 1
            exp_score  = 1 if exp_ok else (anos_cand / anos_min if anos_min else 1)
            score      = round(comp_score * 0.75 + exp_score * 0.25, 2)
            row = {
                "candidato":         _literal(cand, REC.nomeCompleto),
                "vaga":              _literal(vaga, REC.tituloVaga),
                "empresa":           _literal(emp,  REC.nomeEmpresa) if emp else "",
                "score":             score,
                "compativel":        not falta and exp_ok,
                "competenciasOk":    ok_count,
                "competenciasTotal": len(req_comps),
                "competenciasFalta": falta,
                "anosCandidato":     anos_cand,
                "anosMinimos":       anos_min,
                "experienciaOk":     exp_ok,
            }
            matches.append(row)
            if not row["compativel"]:
                gaps.append(row)

    matches.sort(key=lambda x: (x["vaga"], -x["score"], x["candidato"]))
    gaps.sort(key=lambda x: (x["vaga"], x["candidato"]))

    vocabularios = []
    for comp in set(g.subjects(REC.nomeCompetencia, None)):
        esco = g.value(comp, REC.referenciaESCO)
        if esco:
            vocabularios.append({
                "recurso":     _literal(comp, REC.nomeCompetencia),
                "tipo":        "Competência",
                "alinhamento": "ESCO",
                "link":        str(esco),
            })
    for cand in candidatos:
        vocabularios.append({
            "recurso":     _literal(cand, REC.nomeCompleto),
            "tipo":        "Candidato",
            "alinhamento": "foaf:Person",
            "link":        "http://xmlns.com/foaf/0.1/Person",
        })
    vocabularios.sort(key=lambda x: (x["tipo"], x["recurso"]))

    return jsonify({"vagas": vagas_info, "matches": matches, "gaps": gaps, "vocabularios": vocabularios})

# ============================================================
#  API — FACTOS INFERIDOS
# ============================================================
@app.route("/api/inferencia")
@login_required
def api_inferencia():
    base = Graph()
    base.parse(OWL_PATH, format="xml")
    base_triplos = set(base)
    props_interesse = {
        RDF.type, RDFS.subClassOf, RDFS.subPropertyOf,
        REC.possuiCompetencia, REC.requerCompetencia,
        OWL.sameAs, OWL.equivalentClass,
    }
    factos = []
    for s, p, o in g:
        if (s, p, o) in base_triplos: continue
        if p not in props_interesse:  continue
        def fmt(node):
            n = str(node)
            if "#" in n: return n.split("#")[-1]
            return n[:60] + ("…" if len(n) > 60 else "")
        factos.append({"sujeito": fmt(s), "propriedade": fmt(p), "objecto": fmt(o)})
    factos.sort(key=lambda x: (x["propriedade"], x["sujeito"]))
    return jsonify({"total": len(factos), "factos": factos[:80]})

# ============================================================
#  API — MATCH SEMÂNTICO
# ============================================================
@app.route("/api/match")
@login_required
def api_match():
    vaga_id    = request.args.get("vaga","")
    cand_id    = request.args.get("candidato","")
    vaga_comps = set(str(o).split("#")[-1] for o in g.objects(REC[vaga_id], REC.requerCompetencia))
    cand_comps = set(str(o).split("#")[-1] for o in g.objects(REC[cand_id], REC.possuiCompetencia))
    gap_ids    = vaga_comps - cand_comps
    gap_nomes  = []
    for gid in gap_ids:
        nome = g.value(REC[gid], REC.nomeCompetencia)
        gap_nomes.append(str(nome) if nome else gid)
    vaga_anos  = [float(o) for exp in g.objects(REC[vaga_id], REC.requerExperienciaMinima) for o in g.objects(exp, REC.anosExperiencia)]
    cand_anos  = [float(o) for exp in g.objects(REC[cand_id], REC.possuiExperiencia) for o in g.objects(exp, REC.anosExperiencia)]
    min_anos   = max(vaga_anos) if vaga_anos else 0
    total_anos = max(cand_anos) if cand_anos else 0
    exp_ok     = total_anos >= min_anos
    if not exp_ok:
        gap_nomes.append(f"experiencia minima: {min_anos:g} anos")
    return jsonify({
        "compativel": len(gap_ids) == 0 and exp_ok,
        "gap": gap_nomes,
        "experiencia": {"candidato": total_anos, "minima": min_anos}
    })

# ============================================================
#  API — PERFIL DO CANDIDATO LOGADO
# ============================================================
@app.route("/api/meu-perfil")
@login_required
def api_meu_perfil():
    cand_id = session.get("candidato_id")
    if not cand_id:
        return jsonify({"ok": False, "erro": "Não tens perfil de candidato associado."}), 404
    node = REC[cand_id]
    nome  = g.value(node, REC.nomeCompleto)
    email = g.value(node, REC.email)
    tel   = g.value(node, REC.telefone)
    anos  = g.value(node, REC.anoExperiencia)
    form_node = next(iter(g.objects(node, REC.possuiFormacao)), None)
    grau  = g.value(form_node, REC.grauFormacao)  if form_node else None
    area  = g.value(form_node, REC.areaFormacao)  if form_node else None
    comps = []
    for c in g.objects(node, REC.possuiCompetencia):
        n = g.value(c, REC.nomeCompetencia)
        if n: comps.append(str(n))
    return jsonify({
        "ok": True,
        "id": cand_id,
        "nome":  str(nome)  if nome  else "",
        "email": str(email) if email else "",
        "tel":   str(tel)   if tel   else "",
        "anos":  str(anos)  if anos  else "0",
        "grau":  str(grau)  if grau  else "",
        "area":  str(area)  if area  else "",
        "competencias": comps,
    })

@app.route("/api/minhas-candidaturas")
@login_required
def api_minhas_candidaturas():
    cand_id = session.get("candidato_id")
    if not cand_id:
        return jsonify([])
    out = []
    for candidatura in g.objects(REC[cand_id], REC.submeteCandidatura):
        vaga   = g.value(candidatura, REC.refereVaga)
        emp    = g.value(vaga, REC.isPublicadaPor) if vaga else None
        estado = str(g.value(candidatura, REC.estadoCandidatura) or "Em análise")
        etapa  = str(g.value(candidatura, REC.etapaSeleção) or "Triagem")
        data   = g.value(candidatura, REC.dataCandidatura)
        pont   = g.value(candidatura, REC.pontuacaoMatch)
        carta  = g.value(candidatura, REC.cartaMotivacao)
        # Histórico
        hist = []
        for h in g.objects(candidatura, REC.temHistorico):
            hist.append({
                "etapa": str(g.value(h, REC.etapaHistorico) or ""),
                "data":  str(g.value(h, REC.dataHistorico)  or ""),
                "nota":  str(g.value(h, REC.notaHistorico)  or ""),
            })
        hist.sort(key=lambda x: x["data"])
        out.append({
            "id":          str(candidatura).split("#")[-1],
            "tituloVaga":  str(g.value(vaga, REC.tituloVaga)  or "") if vaga else "",
            "nomeEmpresa": str(g.value(emp,  REC.nomeEmpresa) or "") if emp  else "",
            "local":       str(g.value(vaga, REC.localVaga)   or "") if vaga else "",
            "modalidade":  str(g.value(vaga, REC.modalidade)  or "") if vaga else "",
            "estado":      estado,
            "etapa":       etapa,
            "data":        str(data) if data else "",
            "pontuacao":   str(pont) if pont else "",
            "carta":       str(carta) if carta else "",
            "historico":   hist,
        })
    out.sort(key=lambda x: x["data"], reverse=True)
    return jsonify(out)

@app.route("/api/vagas/<vid>/detalhes")
@login_required
def api_vaga_detalhes(vid):
    node = REC[vid]
    titulo   = g.value(node, REC.tituloVaga)
    local    = g.value(node, REC.localVaga)
    modal    = g.value(node, REC.modalidade)
    salario  = g.value(node, REC.salario)
    data     = g.value(node, REC.dataPublicacao)
    desc     = g.value(node, REC.descricaoVaga)
    emp      = g.value(node, REC.isPublicadaPor)
    emp_nome = g.value(emp,  REC.nomeEmpresa)  if emp else None
    emp_sec  = g.value(emp,  REC.sector)        if emp else None
    exp_min  = None
    for exp_node in g.objects(node, REC.requerExperienciaMinima):
        val = g.value(exp_node, REC.anosExperiencia)
        if val: exp_min = str(val)
    comps = []
    for c in g.objects(node, REC.requerCompetencia):
        n = g.value(c, REC.nomeCompetencia)
        if n: comps.append(str(n))
    return jsonify({
        "id":          vid,
        "titulo":      str(titulo)   if titulo   else "",
        "local":       str(local)    if local    else "",
        "modalidade":  str(modal)    if modal    else "",
        "salario":     str(salario)  if salario  else "",
        "data":        str(data)     if data     else "",
        "descricao":   str(desc)     if desc     else "",
        "empresa":     str(emp_nome) if emp_nome else "",
        "sector":      str(emp_sec)  if emp_sec  else "",
        "expMinima":   exp_min or "0",
        "competencias": comps,
    })

# ============================================================
#  ENTRY POINT
# ============================================================
if __name__ == "__main__":
    print("=" * 55)
    print("  Recrutamento Semântico — ISPH")
    print("  http://localhost:5000")
    print()
    print("  Contas de demo:")
    print("    recrutador / isph2025")
    print("    candidato  / cand123    (Nunes Ndala Samba)")
    print("    manuel     / manuel123  (Manuel Alfredo Tchalocano)")
    print()
    if not MAIL_ENABLE:
        print("  [Email] Modo simulado (sem MAIL_USER/MAIL_PASS)")
        print("  Para activar: export MAIL_USER=... MAIL_PASS=...")
    print("=" * 55)
    app.run(debug=True, port=5000)