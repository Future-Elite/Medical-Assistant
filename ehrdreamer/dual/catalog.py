"""Action vocabulary for LLM planning; no fitted or manual drug-response rates."""
from ehrdreamer.catalog import CONDITIONS, PHASES, TREATMENTS as _NAMES

TREATMENTS = {key:{'name':entry['name'],
                   'parameters':(['fio2','pressure_cm_h2o'] if key=='cpap' else ['fio2'] if key=='oxygen' else []),
                   'level':'abstract_neonatal_intervention_no_individual_prescription'}
              for key,entry in _NAMES.items() if key!='supportive_care'}
TREATMENTS.update({key: {'name': name, 'parameters': parameters,
    'level': 'abstract_neonatal_intervention_no_individual_prescription'} for key, name, parameters in [
    ('nippv', '经鼻间歇正压通气', ['fio2']),
    ('invasive_ventilation', '有创呼吸支持评估与实施', ['fio2']),
    ('caffeine', '咖啡因治疗', []), ('feeding_support', '喂养及营养支持', []),
    ('fluid_electrolyte', '液体及电解质管理', []), ('transfusion', '输血支持评估与实施', [])]})
