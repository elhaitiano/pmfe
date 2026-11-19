"""Official PROAGUA questionnaire, versioned independently of learning tests."""
import hashlib,json
from decimal import Decimal
SECTIONS=[('global','Avaliação Global da Formação'),('method','Metodologia'),('logistics','Logística'),('trainer','Desempenho do(s) Formador(es)')]
TEXTS=[
('global','Esta formação cumpriu os seus objetivos?'),
('global','Como avalia o conteúdo global desta formação?'),
('global','O conteúdo da formação foi adequado à função que desempenha?'),
('global','Aconselharia outra pessoa/ colega a fazer esta formação?'),
('method','Os objectivos da formação foram claros?'),
('method','Os métodos utilizados foram adequados (formato, pedagogia, equilíbrio teoria/prática)?'),
('method','A duração da formação, relativamente ao seu conteúdo, foi adequada?'),
('method','O material de apoio distribuído foi adequado em qualidade e quantidade?'),
('method','Os meios audiovisuais utilizados foram adequados (slide, artigos, vídeos, utilização de tablets, etc.)?'),
('logistics','O apoio prestado pelos técnicos da área de formação foi adequado?'),
('logistics','O local onde decorreu a formação era adequado?'),
('logistics','Como avalia as pausas e refeições?'),
('trainer','Transmitiu com clareza os assuntos abordados?'),
('trainer','Conseguiu motivar a participação dos formandos?'),
('trainer','Dominava o assunto que expôs?')]
SCHEMA={'sections':[{'key':k,'title':t} for k,t in SECTIONS],
 'questions':[{'key':f'q{i:02}','section':s,'text':t,'kind':'yesno' if i==4 else 'rating'} for i,(s,t) in enumerate(TEXTS,1)],
 'open':[{'key':'improvements','text':'Que sugestões de melhoria gostaria de fazer?'},{'key':'future_training','text':'Que outras acções de formação gostaria de frequentar?'}],
 'scale':['Insuficiente','Suficiente','Bom','Muito Bom','Excelente']}
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def question_metrics(schema,counts):
    result=[]
    for q in schema['questions']:
        values=counts.get(q['key'],[0]*5);n=sum(values);points=sum((i+1)*v for i,v in enumerate(values));average=Decimal(points)/n if n else None
        result.append(dict(**q,counts=values,n=n,points=points,average=average,percent=average*20 if average is not None else None,
            yes_percent=Decimal(values[4])*100/n if q['kind']=='yesno' and n else None))
    dimensions=[]
    for s in schema['sections']:
        members=[q for q in result if q['section']==s['key'] and q['kind']=='rating'];n=sum(q['n'] for q in members);points=sum(q['points'] for q in members)
        average=Decimal(points)/n if n else None
        dimension={**s,'title':'Satisfação com a formação' if s['key']=='global' else s['title']}
        dimensions.append(dict(**dimension,n=n,points=points,average=average,percent=average*20 if average is not None else None))
    return result,dimensions
