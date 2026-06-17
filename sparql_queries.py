"""
=============================================================
  CONSULTAS SPARQL — Ontologia de Recrutamento Semântico
  Autores: Nunes Ndala Samba · Manuel Alfredo Tchalocano
  Instituto Superior Politécnico da Huíla — ISPH
  Disciplina: Web Semântica · Docente: Faby Sapeth
=============================================================
  Dependência: pip install rdflib
  Uso:         python sparql_queries.py
=============================================================
"""

from rdflib import Graph, Namespace, RDF
from rdflib.namespace import RDFS, OWL, XSD, FOAF

# --- Carrega a ontologia ---
g = Graph()
g.parse("recrutamento.owl", format="xml")

REC = Namespace("http://www.semanticweb.org/isph/recrutamento#")

PREFIXOS = """
    PREFIX rec: <http://www.semanticweb.org/isph/recrutamento#>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
    PREFIX foaf: <http://xmlns.com/foaf/0.1/>
"""

def executar(titulo, query, colunas):
    """Executa uma query SPARQL e imprime os resultados formatados."""
    print("\n" + "=" * 60)
    print(f"  {titulo}")
    print("=" * 60)
    resultados = list(g.query(PREFIXOS + query))
    if not resultados:
        print("  (sem resultados)")
        return resultados
    # cabeçalho
    larguras = [max(len(c), max((len(str(r[i])) for r in resultados), default=0))
                for i, c in enumerate(colunas)]
    linha = "  " + " | ".join(c.ljust(larguras[i]) for i, c in enumerate(colunas))
    print(linha)
    print("  " + "-" * (sum(larguras) + 3 * len(colunas)))
    for row in resultados:
        vals = [str(v).split("#")[-1] if v else "-" for v in row]
        print("  " + " | ".join(v.ljust(larguras[i]) for i, v in enumerate(vals)))
    return resultados


# ============================================================
# Q1 — Todos os candidatos e as suas competências
# ============================================================
executar(
    "Q1 · Candidatos e as suas competências",
    """
    SELECT ?nomeCandidato ?nomeCompetencia ?nivel
    WHERE {
        ?c rdf:type rec:Candidato .
        ?c rec:nomeCompleto ?nomeCandidato .
        ?c rec:possuiCompetencia ?comp .
        ?comp rec:nomeCompetencia ?nomeCompetencia .
        ?comp rec:nivelCompetencia ?nivel .
    }
    ORDER BY ?nomeCandidato ?nomeCompetencia
    """,
    ["Candidato", "Competência", "Nível"]
)


# ============================================================
# Q2 — Vagas disponíveis e a empresa que as publica
# ============================================================
executar(
    "Q2 · Vagas disponíveis e empresa publicadora",
    """
    SELECT ?titulo ?empresa ?local ?modalidade ?salario
    WHERE {
        ?v rdf:type rec:Vaga .
        ?v rec:tituloVaga ?titulo .
        ?v rec:localVaga ?local .
        ?v rec:modalidade ?modalidade .
        ?v rec:salario ?salario .
        ?v rec:isPublicadaPor ?emp .
        ?emp rec:nomeEmpresa ?empresa .
    }
    ORDER BY ?titulo
    """,
    ["Título", "Empresa", "Local", "Modalidade", "Salário (AOA)"]
)


# ============================================================
# Q3 — Matching semântico: candidatos que possuem TODAS as
#       competências exigidas por uma vaga
# ============================================================
executar(
    "Q3 · Matching semântico — candidatos compatíveis com cada vaga",
    """
    SELECT ?nomeCandidato ?tituloVaga ?nomeEmpresa
    WHERE {
        ?cand rdf:type rec:Candidato .
        ?cand rec:nomeCompleto ?nomeCandidato .

        ?vaga rdf:type rec:Vaga .
        ?vaga rec:tituloVaga ?tituloVaga .
        ?vaga rec:isPublicadaPor ?emp .
        ?emp rec:nomeEmpresa ?nomeEmpresa .

        # O candidato possui TODAS as competências que a vaga requer
        FILTER NOT EXISTS {
            ?vaga rec:requerCompetencia ?comp .
            FILTER NOT EXISTS { ?cand rec:possuiCompetencia ?comp }
        }
    }
    ORDER BY ?tituloVaga ?nomeCandidato
    """,
    ["Candidato", "Vaga", "Empresa"]
)


# ============================================================
# Q4 — Estado das candidaturas com pontuação de compatibilidade
# ============================================================
executar(
    "Q4 · Estado das candidaturas e pontuação de match",
    """
    SELECT ?nomeCandidato ?tituloVaga ?estado ?pontuacao
    WHERE {
        ?cand rdf:type rec:Candidato .
        ?cand rec:nomeCompleto ?nomeCandidato .
        ?cand rec:submeteCandidatura ?candidatura .
        ?candidatura rec:estadoCandidatura ?estado .
        ?candidatura rec:pontuacaoMatch ?pontuacao .
        ?candidatura rec:refereVaga ?vaga .
        ?vaga rec:tituloVaga ?tituloVaga .
    }
    ORDER BY DESC(?pontuacao)
    """,
    ["Candidato", "Vaga", "Estado", "Pontuação"]
)


# ============================================================
# Q5 — Candidatos com entrevistas agendadas/realizadas
# ============================================================
executar(
    "Q5 · Entrevistas: candidato, vaga e resultado",
    """
    SELECT ?nomeCandidato ?tituloVaga ?tipoEntrevista ?resultado
    WHERE {
        ?cand rdf:type rec:Candidato .
        ?cand rec:nomeCompleto ?nomeCandidato .
        ?cand rec:submeteCandidatura ?candidatura .
        ?candidatura rec:refereVaga ?vaga .
        ?vaga rec:tituloVaga ?tituloVaga .
        ?candidatura rec:geraEntrevista ?entrev .
        ?entrev rec:tipoEntrevista ?tipoEntrevista .
        ?entrev rec:resultadoEntrevista ?resultado .
    }
    """,
    ["Candidato", "Vaga", "Tipo Entrevista", "Resultado"]
)


# ============================================================
# Q6 — Vagas com requisitos que o candidato NÃO satisfaz
#       (gap de competências por candidato)
# ============================================================
executar(
    "Q6 · Gap de competências — o que falta a cada candidato por vaga",
    """
    SELECT ?nomeCandidato ?tituloVaga ?competenciaEmFalta
    WHERE {
        ?cand rdf:type rec:Candidato .
        ?cand rec:nomeCompleto ?nomeCandidato .

        ?vaga rdf:type rec:Vaga .
        ?vaga rec:tituloVaga ?tituloVaga .
        ?vaga rec:requerCompetencia ?comp .
        ?comp rec:nomeCompetencia ?competenciaEmFalta .

        FILTER NOT EXISTS { ?cand rec:possuiCompetencia ?comp }
    }
    ORDER BY ?nomeCandidato ?tituloVaga
    """,
    ["Candidato", "Vaga", "Competência em Falta"]
)


# ============================================================
# Q7 — Candidatos ordenados por anos de experiência
# ============================================================
executar(
    "Q7 · Candidatos ordenados por experiência profissional",
    """
    SELECT ?nome ?anos ?grau ?area
    WHERE {
        ?c rdf:type rec:Candidato .
        ?c rec:nomeCompleto ?nome .
        ?c rec:anoExperiencia ?anos .
        ?c rec:possuiFormacao ?form .
        ?form rec:grauFormacao ?grau .
        ?form rec:areaFormacao ?area .
    }
    ORDER BY DESC(?anos)
    """,
    ["Nome", "Anos Exp.", "Grau", "Área"]
)


# ============================================================
# Q8 — Contagem de candidatos por vaga (AGGREGATE)
# ============================================================
executar(
    "Q8 · Número de candidatos por vaga",
    """
    SELECT ?tituloVaga (COUNT(?cand) AS ?totalCandidatos)
    WHERE {
        ?vaga rdf:type rec:Vaga .
        ?vaga rec:tituloVaga ?tituloVaga .
        ?candidatura rec:refereVaga ?vaga .
        ?cand rec:submeteCandidatura ?candidatura .
    }
    GROUP BY ?tituloVaga
    ORDER BY DESC(?totalCandidatos)
    """,
    ["Vaga", "Total Candidatos"]
)



# ============================================================
# Q9 — Histórico profissional reificado por candidato
# ============================================================
executar(
    "Q9 · Histórico profissional reificado",
    """
    SELECT ?nome ?cargo ?empresa ?inicio ?fim ?anos
    WHERE {
        ?c rdf:type rec:Candidato .
        ?c rec:nomeCompleto ?nome .
        ?c rec:possuiExperiencia ?exp .
        ?exp rec:cargoExperiencia ?cargo .
        OPTIONAL { ?exp rec:empresaExperiencia ?empresa }
        OPTIONAL { ?exp rec:dataInicioExperiencia ?inicio }
        OPTIONAL { ?exp rec:dataFimExperiencia ?fim }
        OPTIONAL { ?exp rec:anosExperiencia ?anos }
    }
    ORDER BY ?nome DESC(?inicio)
    """,
    ["Candidato", "Cargo", "Empresa", "Início", "Fim", "Anos"]
)


# ============================================================
# Q10 — Competências alinhadas com referências ESCO
# ============================================================
executar(
    "Q10 · Competências com referência ESCO",
    """
    SELECT ?competencia ?categoria ?esco
    WHERE {
        ?comp rec:nomeCompetencia ?competencia .
        OPTIONAL { ?comp rec:categoriaCompetencia ?categoria }
        OPTIONAL { ?comp rec:referenciaESCO ?esco }
    }
    ORDER BY ?competencia
    """,
    ["Competência", "Categoria", "Referência ESCO"]
)


# ============================================================
# Q11 — Alinhamento FOAF dos candidatos
# ============================================================
executar(
    "Q11 · Candidatos alinhados com FOAF",
    """
    SELECT ?candidato ?nomeFoaf ?emailFoaf
    WHERE {
        ?candidato rdf:type foaf:Person .
        OPTIONAL { ?candidato foaf:name ?nomeFoaf }
        OPTIONAL { ?candidato foaf:mbox ?emailFoaf }
    }
    ORDER BY ?nomeFoaf
    """,
    ["Candidato", "foaf:name", "foaf:mbox"]
)


print("\n" + "=" * 60)
print("  Todas as consultas SPARQL executadas com sucesso.")
print("=" * 60 + "\n")
