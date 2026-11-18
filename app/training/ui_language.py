import re

def display_label(value):
    if not isinstance(value,str):
        return value
    pairs=[('Cadastros','Registos'),('cadastros','registos'),('Cadastro','Registo'),('cadastro','registo'),('Turmas','Grupos'),('turmas','grupos'),('Turma','Grupo'),('turma','grupo')]
    for old,new in pairs:
        value=re.sub(r'\b'+old+r'\b',new,value)
    return value
