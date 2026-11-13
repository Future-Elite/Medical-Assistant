"""Local medical concept graph and relation traversal retrieval, without an LLM indexer."""
from __future__ import annotations

import copy
import json
import re
from collections import Counter, defaultdict, deque
from functools import lru_cache

from ehrworld.io import ROOT
from .catalog import TREATMENTS
from .evidence import EVIDENCE_VERSION, SOURCES, RECOMMENDATIONS, CONCEPTS, TREATMENT_ALIASES, PANELS, CONSTRAINT_ONLY, LOCAL_BINDINGS


def normal(text):
    return re.sub(r'\s+', '', text).replace('₂','2').casefold()


def contains_alias(text, alias):
    text,alias=normal(text),normal(alias)
    if re.fullmatch(r'[a-z0-9_+()\-]+',alias):
        return re.search(r'(?<![a-z0-9])'+re.escape(alias)+r'(?![a-z0-9])',text) is not None
    return alias in text


def visible_facts(visible):
    intake = visible['intake']
    age = intake.get('postnatal_age_hours')
    latest = {i['key']: {**i, 'sampled_at_hours': o['collected_at_hours']}
              for o in visible['observations'] for i in o['items']}
    flags = (set(intake.get('symptoms', [])) | set(intake.get('clinical_findings',{}).get('red_flags',[]))) & {'呼吸暂停', '惊厥', '休克', '需心肺复苏'}
    weeks=intake.get('gestational_weeks');days=intake.get('gestational_days')
    return {'ga_weeks': None if weeks is None else weeks+(days or 0)/7,
            'ga_precision':'weeks_only' if days is None else 'weeks_and_days',
            'birth_weight_g':intake.get('birth_weight_g'),
            'age_hours': None if age is None else age+visible['time_hours'],
            'symptoms': intake.get('symptoms', []), 'risks': intake.get('perinatal_risks', []),
            'clinical_findings': copy.deepcopy(intake.get('clinical_findings', {})),
            'red_flags': sorted(flags), 'active_targets': [a['target'] for a in visible['active_treatments']],
            'known_metric_keys': list(latest)}


def population_match(population, facts):
    unknown = []
    for key, limit in population.items():
        if key=='preterm_or_low_birth_weight':
            ga,weight=facts.get('ga_weeks'),facts.get('birth_weight_g')
            if ga is not None and ga<37 or weight is not None and weight<2500:continue
            if ga is None or weight is None:unknown.append('胎龄或出生体重');continue
            return 'not_applicable',[]
        value = facts.get('ga_weeks' if key.startswith('ga_') else 'age_hours')
        if value is None:
            unknown.append('胎龄' if key.startswith('ga_') else '当前日龄')
            continue
        if key=='ga_min_exclusive' and value==limit and facts.get('ga_precision')=='weeks_only':
            unknown.append('精确孕周天数');continue
        ok = value >= limit if key.endswith('_min') else value > limit if key.endswith('_min_exclusive') else value < limit if key.endswith('_max_exclusive') else value <= limit
        if not ok:
            return 'not_applicable', []
    return ('conditional' if unknown else 'population_matches'), sorted(set(unknown))


class MedicalKnowledgeGraph:
    def __init__(self):
        self.nodes = {}
        self.edges = []
        self.adjacency = defaultdict(list)
        self.rules = {}
        self._edge_ids = set()
        for key, (label, aliases) in CONCEPTS.items(): self.node('concept:'+key, 'concept', label, aliases=aliases)
        for key, aliases in TREATMENT_ALIASES.items():
            self.node('action:'+key, 'action', TREATMENTS.get(key, {}).get('name', aliases[0]), aliases=[key, *aliases], canonical_id=key)
        for key, (label, aliases) in PANELS.items(): self.node('exam:'+key, 'exam', label, aliases=aliases)
        for key, source in SOURCES.items(): self.node('source:'+key, 'source', source['title'], **source)
        # Original local PDF summaries stay traceable; primary recommendations
        # with the same ID take precedence over a secondary interpretation.
        from ehrworld.web.guidelines import RULES as LOCAL_RULES, SOURCES as LOCAL_SOURCES, citation
        for key, source in LOCAL_SOURCES.items():
            self.node('source:'+key, 'source', source['title'], url='/api/guidelines/'+key+'/document', kind='local_guideline', scope=source['scope'])
        rows = list(RECOMMENDATIONS)
        existing = {r[0] for r in rows}
        for key in LOCAL_RULES:
            if key in existing: continue
            rule = citation(key)
            tags = ['pneumonia', 'infection'] if key.startswith('PN-') else ['rds', 'respiratory']
            actions,panels=LOCAL_BINDINGS.get(key,([],[]))
            rows.append((key, rule['summary'], [rule['source_id']], tags, actions, panels,
                         {'ga_max_exclusive':37} if key.startswith('RDS-') else {}))
        for identifier, summary, sources, concepts, actions, panels, population in rows:
            constraints=actions if identifier in CONSTRAINT_ONLY else []
            recommended=[] if identifier in CONSTRAINT_ONLY else actions
            rule = {'id': identifier, 'summary': summary, 'source_ids': sources, 'concepts': concepts,
                    'actions': recommended, 'constrained_actions':constraints,'monitoring_panels': panels, 'population': population,
                    'curation_status': 'source_checked_implementation_summary_not_clinician_validated'}
            self.rules[identifier] = rule
            rid = 'recommendation:'+identifier
            self.node(rid, 'recommendation', identifier, summary=summary)
            for concept in concepts: self.edge('concept:'+concept, rid, 'ADDRESSED_BY')
            for action in recommended: self.edge(rid, 'action:'+action, 'RECOMMENDS')
            for action in constraints:self.edge(rid,'action:'+action,'CONSTRAINS')
            for panel in panels: self.edge(rid, 'exam:'+panel, 'MONITOR_WITH')
            for source in sources: self.edge(rid, 'source:'+source, 'SUPPORTED_BY')
            if population:
                pid = 'population:'+identifier
                self.node(pid, 'population', json.dumps(population, ensure_ascii=False), constraints=population)
                self.edge(rid, pid, 'APPLIES_TO')

    def node(self, identifier, node_type, label, **properties):
        self.nodes[identifier] = {'id': identifier, 'type': node_type, 'label': label, **properties}

    def edge(self, source, target, relation):
        if source not in self.nodes or target not in self.nodes:
            raise ValueError('Graph edge has an unresolved node')
        identity = (source, relation, target)
        if identity in self._edge_ids: return
        self._edge_ids.add(identity)
        edge = {'source': source, 'relation': relation, 'target': target}
        self.edges.append(edge)
        self.adjacency[source].append((target, edge))
        self.adjacency[target].append((source, edge))

    def resolve(self, query):
        q = normal(query)
        exact, partial = [], []
        for node in self.nodes.values():
            if node['type'] not in ('concept', 'action', 'exam'): continue
            aliases = [node['id'], node['label'], *node.get('aliases', [])]
            if any(q == normal(a) for a in aliases): exact.append(node['id'])
            elif any(len(normal(a)) >= 2 and contains_alias(q,a) for a in aliases): partial.append(node['id'])
        return exact or partial

    def canonical_action(self, value):
        if value in TREATMENTS: return value
        matches = [self.nodes[k]['canonical_id'] for k in self.resolve(value)
                   if self.nodes[k]['type'] == 'action' and self.nodes[k]['canonical_id'] in TREATMENTS]
        return matches[0] if len(set(matches)) == 1 else value

    def retrieve(self, queries=(), visible=None, limit=18):
        facts = visible_facts(visible) if visible else {}
        seeds = {'concept:assessment', 'concept:laboratory', 'concept:observation', 'concept:safety'}
        requested = list(queries)
        if visible:
            requested += list(facts['symptoms'])+list(facts['risks'])+facts['active_targets']
            latest={i['key']:i for report in visible['observations'] for i in report['items']}
            requested += [i['name'] for i in latest.values() if i.get('status') in ('high','low') or i.get('source_flag') in ('↑','↓')]
            if facts.get('ga_weeks') is not None and facts['ga_weeks'] < 37: seeds.add('concept:rds')
            if set(facts['risks'])&{'母体感染风险','胎膜早破'}: seeds.add('concept:infection')
        for query in requested: seeds.update(self.resolve(query))
        # Traverse typed graph edges rather than matching recommendation prose.
        paths = defaultdict(list)
        for seed in sorted(seeds):
            queue = deque([(seed, [seed], [])])
            while queue:
                current, nodes, edges = queue.popleft()
                if self.nodes[current]['type'] == 'recommendation':
                    paths[current.split(':', 1)[1]].append({'nodes': nodes, 'edges': edges})
                    continue
                if len(edges) >= 2: continue
                for nxt, edge in self.adjacency[current]:
                    if nxt not in nodes and self.nodes[nxt]['type'] in ('concept', 'action', 'exam', 'recommendation'):
                        queue.append((nxt, [*nodes, nxt], [*edges, edge]))
        pinned = {'CARE-ASSESS', 'OBS-LOW-RISK', 'EOS-RED-FLAGS', 'EOS-LABS-LIMIT'}
        ranked = sorted(paths, key=lambda k: (k not in pinned, -len(paths[k]), k))
        selected, excluded = [], []
        used_nodes = set(seeds); used_edges = []
        for key in ranked:
            rule = self.rules[key]
            status, missing = population_match(rule['population'], facts)
            entry = {**copy.deepcopy(rule), 'population_match': status, 'missing_population_facts': missing,
                     'evidence_paths': paths[key][:3]}
            if status == 'not_applicable':
                excluded.append(entry)
                continue
            if len(selected) >= limit: continue
            selected.append(entry)
            rid = 'recommendation:'+key
            for path in paths[key][:3]:
                used_nodes.update(path['nodes']); used_edges.extend(path['edges'])
            for target, edge in self.adjacency[rid]:
                if self.nodes[target]['type'] in ('source', 'population', 'action', 'exam'):
                    used_nodes.add(target); used_edges.append(edge)
        unique_edges = {tuple(e.values()): e for e in used_edges}
        sources = {n.split(':', 1)[1]: {k: v for k, v in self.nodes[n].items() if k not in ('id', 'type', 'label')}
                   for n in used_nodes if self.nodes[n]['type'] == 'source'}
        panels=[];metric_nodes={};metric_edges=[]
        if visible:
            latest={i['key']:i for report in visible['observations'] for i in report['items']}
            for panel,(label,aliases) in PANELS.items():
                members=[item for item in latest.values() if any(contains_alias(item['name'],a) or contains_alias(item['key'],a) for a in aliases)]
                if not members:continue
                panels.append({'id':panel,'label':label,'metric_count':len(members),'metric_keys':[i['key'] for i in members]})
                pid='exam:'+panel
                used_nodes.add(pid)
                for item in members:
                    mid='metric:'+item['key']
                    metric_nodes[mid]={'id':mid,'type':'patient_metric','label':item['name'],'unit':item['unit']}
                    metric_edges.append({'source':pid,'relation':'CONTAINS_INITIALIZED_METRIC','target':mid})
        return {'version': EVIDENCE_VERSION, 'queries': list(queries), 'effective_queries':list(dict.fromkeys(requested)),
                'normalized_terms': [self.nodes[k] for k in sorted(seeds)],
                'available_monitoring_panels':panels,
                'patient_facts': facts, 'recommendations': selected,
                'excluded_recommendations': [{k: r[k] for k in ('id', 'population', 'population_match')} for r in excluded],
                'sources': sources, 'graph': {'nodes': [self.nodes[k] for k in sorted(used_nodes)]+list(metric_nodes.values()), 'edges': list(unique_edges.values())+metric_edges},
                'boundary': '人群匹配不代表临床指征已满足；不得把研究结论转写成确定个体药效。'}

    def stats(self):
        path = ROOT/'outputs/neonatal_evidence/search_manifest.json'
        try:discovery = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        except (ValueError,OSError):discovery={'status':'local_discovery_manifest_unavailable'}
        return {'version': EVIDENCE_VERSION, 'node_count': len(self.nodes), 'edge_count': len(self.edges),
                'node_types': dict(Counter(n['type'] for n in self.nodes.values())), 'recommendations': len(self.rules),
                'sources': list(SOURCES.values()),
                'pubmed_discovery': {k: v for k, v in discovery.items() if k not in ('queries', 'missing_pmids')},
                'retrieval': 'canonical term resolution + bounded typed relation traversal + population filtering',
                'not_a_claim_of_exhaustive_world_literature': True}


@lru_cache(maxsize=1)
def get_graph():
    return MedicalKnowledgeGraph()
