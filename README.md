# PMFE

> DEMONSTRAÇÃO: esta branch tem um único commit raiz e histórico independente da main e das branches anteriores. Contém código e um gerador de dados inteiramente fictícios. Crie branches de trabalho a partir desta branch e abra Pull Requests para demo/arquitectura-proagua. Não una o histórico antigo a esta demonstração; uma futura integração na main exige um plano separado.

**Plataforma de Monitorização de Formação EPAL**, no âmbito do projecto PROÁGUA.

A PMFE reúne o cadastro de técnicos, o planeamento de formações, as inscrições, as presenças, os resultados e a entrega de certificados. Permite acompanhar o percurso formativo de cada técnico e produzir indicadores e relatórios para apoiar a gestão.

A aplicação funciona localmente num único computador, através do navegador, com interface em português. Após a instalação das dependências, as funcionalidades principais estão disponíveis sem ligação à Internet.

## Desenvolvimento e colaboração

**Projecto em desenvolvimento.** A branch de demonstração é `demo/arquitectura-proagua`; alterações à `main` passam por revisão. Este repositório contém código, migrações, testes sintéticos e recursos institucionais neutros. Não inclui a base EPAL, resultados individuais reais, certificados, CV, Excel de trabalhadores, backups ou segredos.

As funcionalidades recentes incluem documentos PDF privados, emissão e entregas independentes, avaliações originais e equivalências calculadas, grupos históricos de consulta, filtros etários e **Consultas e Relatórios** com Excel/PDF. Os estados formais do catálogo continuam independentes da verificação técnica e dos resultados individuais.

### Instalação local no Windows / VS Code

Requisitos: Python 3.12 ou superior compatível com Django 6, Git e um ambiente virtual próprio. No terminal PowerShell:

```powershell
git clone --single-branch --branch demo/arquitectura-proagua https://github.com/elhaitiano/pmfe.git
cd pmfe
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r app/requirements-dev.txt

# Utilizar uma pasta NOVA de desenvolvimento; nunca a pasta PMF da instalação real.
$env:PMF_DATA_DIR = "$env:LOCALAPPDATA\PMFE_DEV"
$env:PMF_DOCUMENTS_ENABLED = "1"
$env:PMF_HISTORICAL_ENABLED = "1"
$env:PMF_DEMO_MODE = "1"
cd app
& ..\.venv\Scripts\python.exe -B manage.py migrate --settings=pmf.settings
& ..\.venv\Scripts\python.exe -B manage.py seed_demo --confirm-demo
& ..\.venv\Scripts\python.exe -B manage.py createsuperuser
& ..\.venv\Scripts\python.exe -B manage.py check
& ..\.venv\Scripts\python.exe -B manage.py runserver 127.0.0.1:8000 --noreload --settings=pmf.settings
```

Os comandos `migrate` e `seed_demo` acima destinam-se **apenas à base nova do colaborador**. Não executar na base real `%LOCALAPPDATA%\PMF\pmf.sqlite3`. A demonstração exige confirmação, recusa a pasta real PMF, uma base com dados, índices existentes e caminhos externos configurados. Não cria utilizadores nem palavras-passe. `createsuperuser` solicita as credenciais localmente; não as guardar no Git.

O comando cria dados independentes, sem copiar registos institucionais:

- 8 formandos `DEMO-001` a `DEMO-008`, com direcções, funções, contactos `000…` e e-mails `example.invalid` fictícios;
- 3 acções, incluindo estados pendente, validado e bloqueado **apenas de demonstração**, sem aprovação institucional;
- 6 participações históricas em 2 edições sintéticas, com presenças verificadas e desconhecidas, notas /20 (incluindo zero), uma percentagem final de 80% e uma classificação qualitativa;
- 1 turma de planeamento fictícia, 3 inscrições e 2 sessões não consecutivas, separadas do histórico;
- 3 estados documentais que distinguem emissão, entrega ao formando e entrega aos RH;
- 1 CV e 1 certificado PDF inteiramente gerados, marcados **DEMONSTRAÇÃO — SEM VALIDADE**, no arquivo privado, sem URLs públicas.

Os dados ficam exclusivamente na nova base de desenvolvimento. O modo `PMF_DEMO_MODE=1` apresenta uma identificação visual e impede a leitura dos índices privados antigos. Uma falha na criação dos índices faz rollback dos dados e remove apenas os ficheiros criados nessa execução. O comando recusa uma segunda execução sobre a mesma base. Para recomeçar, escolha outra pasta nova; não apague a instalação real.

Abrir `http://127.0.0.1:8000/`, `/planeamento/`, `/planeamento/historico/`, `/consultas/` e `/dashboard/`. Para parar, usar Ctrl+C no mesmo terminal. Não iniciar uma segunda instância na porta ocupada.

Mantenha `PMF_DEMO_MODE=1` durante a demonstração, para conservar a identificação e a separação dos índices. Os utilizadores de consulta precisam de `training.view_employee` e `training.view_training`; correcções de avaliações exigem também `is_staff` e `training.change_training`. Arquivo e estados documentais têm permissões próprias. Não atribuir permissões automaticamente a utilizadores reais.

### Dados privados opcionais

| Variável de ambiente | Finalidade |
|---|---|
| `PMF_DATA_DIR` | Pasta da base SQLite, chave local e documentos privados; obrigatoriamente isolada no desenvolvimento. |
| `PMF_DOCUMENTS_ENABLED=1` | Activa o módulo documental após as migrações da base correspondente. |
| `PMF_HISTORICAL_ENABLED=1` | Activa páginas de grupos históricos e filtros etários. |
| `PMF_HISTORICAL_DATA_FILE` | Caminho para o índice privado `historical_editions.json`. |
| `PMF_ACHIEVEMENT_DATA_FILE` | Caminho para o índice privado `pilar_achievement.json`. |
| `PMF_DEMO_MODE=1` | Autoriza exclusivamente a preparação explícita da base sintética vazia. |

Os índices não são obrigatórios. A leitura procura primeiro o caminho configurado; depois `PMF_DATA_DIR/private/`; **fora do modo demonstração**, por compatibilidade com a instalação local existente, aceita os antigos ficheiros ignorados em `app/training/data/`. No modo demonstração, só aceita índices identificados como sintéticos na pasta privada da base de desenvolvimento. Se não houver índices, utiliza estruturas vazias. Um caminho explicitamente configurado mas inexistente ou inválido produz um erro claro.

Os suplementos de avaliação já persistidos na base são lidos independentemente de existirem índices externos. Não copiar a base real ou os índices EPAL para o repositório. Se for necessário acesso a dados reais, usar exclusivamente o canal privado e autorizado pela EPAL, fora do Git. Não publicar planos, diagnósticos ou capturas que permitam identificar trabalhadores.

As percentagens finais comprovadas podem apresentar equivalência **percentagem × 20 / 100**, identificada como calculada. O original permanece preservado; assiduidade/satisfação não são notas, classificações qualitativas não são convertidas sem tabela oficial e valores desconhecidos não são zeros. Nenhuma conversão em massa é executada ao iniciar a aplicação.

### Testes isolados

Na pasta `app`, com o ambiente virtual instalado:

```powershell
& ..\.venv\Scripts\python.exe -B test_phase8_isolated.py
# Bateria dirigida ao catálogo e planeamento:
& ..\.venv\Scripts\python.exe -B test_catalogue_isolated.py
```

Os runners configuram SQLite de testes e não importam a configuração de produção. Não precisam de Excel real, índices privados nem da base EPAL. Incluem permissões, CSRF, revisão/concorrência, cálculos Decimal, auditoria/rollback, PDFs/Excel e documentos privados. As migrações 0012, 0013 e 0014 estão incluídas; a verificação de modelos não aplica migrações à base real.

Os testes de navegador usam Playwright com Edge local, sem rede, quando disponível. Para testes apenas de backend não é necessário iniciar o Django. Mantenha `.venv`, ficheiros privados e resultados dos testes fora dos commits. Os avisos de PDF deliberadamente malformado pertencem aos testes de validação.

### Trabalhar sem perder alterações

Criar uma branch pessoal a partir da integração actualizada, por exemplo `feature/nome-consultas`. Fazer commits pequenos, executar testes e abrir Pull Request para `demo/arquitectura-proagua`. A integração na `main` é revista separadamente. Não usar force push, reset destrutivo ou substituição de ficheiros para resolver diferenças. Se o directório de trabalho tiver alterações, preservá-las antes de trocar de branch ou actualizar a cópia local.

Antes de publicar: rever os ficheiros seleccionados, verificar o `.gitignore` e procurar dados pessoais/segredos. Não usar `git add .` sem essa revisão. As actualizações automáticas de dependências, como a branch do Dependabot, devem ter revisão e testes próprios.

## Funcionalidades

| Módulo | O que permite fazer |
| --- | --- |
| Cadastro de técnicos | Pesquisar por nome ou número mecanográfico, criar e corrigir fichas, consultar direcção, categoria e contactos. |
| Histórico de formação | Consultar e actualizar participações, duração, presenças, notas, resultados e entrega de certificados. |
| Ficha individual | Consultar dados e indicadores do técnico, visualizar o histórico e imprimir ou guardar a ficha em PDF pelo navegador. |
| Catálogo de formações | Criar acções com categoria, objectivos e duração prevista; editar ou arquivar acções existentes. |
| Planeamento e turmas | Organizar grupos G1, G2… por acção, com capacidade, local, formador, horários e calendário de dias efectivos, incluindo dias não consecutivos. |
| Inscrições | Seleccionar participantes, rever avisos de histórico, justificar repetições e transferir inscrições entre grupos ainda não iniciados. |
| Actualização colectiva | Registar presenças verificadas, notas, resultados e certificados de uma mesma edição, com revisão antes da confirmação. |
| Listas de presença | Gerar listas diárias em PDF ou Excel segundo o modelo institucional, com espaços para assinatura. |
| Dashboard executivo | Consultar indicadores e gráficos, filtrar por período, direcção, área e situação, e explorar os registos correspondentes. |
| Relatórios e arquivo | Pré-visualizar, gerar, arquivar e descarregar relatórios PDF por período ou direcção. |
| Assistente EPAL | Pesquisar orientações locais, seguir passos e utilizar atalhos. Os administradores podem editar e publicar os guias. |
| Cópias de segurança e auditoria | Exportar a base e os relatórios, conservar cópias antes das migrações e consultar o registo das operações administrativas. |

A eliminação de turmas preparatórias e de acções criadas por engano exige acesso administrativo, motivo e confirmação `ELIMINAR`. Turmas com histórico e acções com grupos ou histórico associado ficam protegidas contra eliminação.

## Percurso de utilização

1. Criar ou seleccionar uma acção no catálogo.
2. Criar um grupo e definir dias, horário, capacidade, local e formador.
3. Seleccionar os técnicos, rever os avisos e confirmar as inscrições.
4. Gerar a lista de presença de cada dia em PDF ou Excel.
5. Iniciar o grupo para criar as participações no histórico.
6. Actualizar presenças verificadas, notas, resultados e entregas de certificados.
7. Consultar indicadores, produzir relatórios e guardar cópias de segurança.

## Regras de tratamento dos dados

- Uma inscrição não confirma presença nem formação realizada.
- **Horas previstas:** dias de formação × horas por dia.
- **Horas realizadas:** dias de presença × horas por dia.
- **Assiduidade individual:** presenças ÷ dias previstos × 100.
- **Assiduidade consolidada:** soma das presenças ÷ soma dos dias elegíveis × 100.
- Uma nota zero é válida e distingue-se de uma nota não preenchida.
- O resultado da formação e a entrega do certificado são informações independentes.
- Zero presenças sem verificação não confirma uma ausência.
- Um PDF arquivado conserva os dados do momento da geração; alterações posteriores não reescrevem o documento.

## Tecnologias e arquitectura

Na instalação Windows de desenvolvimento, o navegador comunica directamente com Django; SQLite e índices opcionais ficam em `PMF_DATA_DIR`. Não é necessário Docker para desenvolver. A arquitectura mantém os seguintes serviços em `app/training/`:

| Responsabilidade | Componentes |
| --- | --- |
| Cadastro, participações e fichas | `models.py`, `forms.py`, `views.py`, `analytics.py` |
| Catálogo, planeamento e revisões | `planning.py`, `catalogue_corrections.py`, formulários e vistas de correcção |
| Histórico de consulta e faixas etárias | `historical.py`, `historical_views.py`, `private_data.py` |
| Avaliações e rastreabilidade | `evaluations.py`, `approved_evaluations.py`, `evaluation_corrections.py` |
| Arquivo PDF privado e entregas | `documents.py`, `document_views.py` |
| Consultas, Excel/PDF e indicadores | `consultations.py`, `consultation_views.py`, `dashboard.py`, `executive.py`, serviços PDF/Excel |
| Interface e evolução do esquema | `app/templates/`, `app/static/`, `app/training/migrations/` |

Autenticação, permissões e auditoria continuam comuns aos módulos. Dados sintéticos não são criados automaticamente no arranque. Os modelos não dependem de um Excel ou índice EPAL para funcionar.

| Componente | Tecnologia |
| --- | --- |
| Backend | Python 3.12, Django e Waitress |
| Interface | Templates Django, HTML, CSS/Tailwind já compilado e JavaScript |
| Serviço frontend | Nginx para ficheiros estáticos e encaminhamento de pedidos para Django |
| Base de dados | SQLite num volume Docker persistente |
| Documentos | ReportLab para PDF, openpyxl para importação Excel e modelo XLSX para listas de presença |
| Execução | Docker Compose |

```text
Navegador — http://localhost:8080
    │
    ▼
frontend: Nginx
    ├── CSS, JavaScript, imagens e fontes locais
    └── backend: Django + Waitress
                    └── volume database
                         ├── pmf.sqlite3
                         ├── secret.key
                         ├── reports/
                         └── backups/
```

Existem **dois serviços Docker**, `frontend` e `backend`. `database` é um volume persistente, não um terceiro contentor. Django gera as páginas HTML no servidor.

## Instalação

### Requisitos

- **Linux:** Docker Engine e Docker Compose instalados e em execução.
- **Windows:** Docker Desktop configurado para contentores Linux.
- Ligação à Internet na primeira construção, caso as imagens e dependências ainda não estejam disponíveis localmente.

Não é necessário instalar Python, Node.js ou Microsoft Excel no computador anfitrião para utilizar a aplicação com Docker.

### Primeiro arranque

Descarregue ou clone o repositório **pmfe**. A partir da pasta que contém `compose.yaml`, execute:

```bash
docker compose config --quiet
docker compose build
docker compose up -d --wait
docker compose exec backend python manage.py createsuperuser
```

Abra **http://localhost:8080** e entre com a conta criada.

Um volume novo começa com uma base vazia. Os dados pessoais, as contas e as cópias de segurança não são distribuídos com o repositório. As migrações são aplicadas automaticamente no arranque do backend.

### Configuração local

O repositório chama-se `pmfe`. O ficheiro Compose define `pmf-containers` como nome de projecto por defeito. Para uma **nova instalação**, pode definir outro nome num ficheiro `.env`, junto de `compose.yaml`:

```dotenv
COMPOSE_PROJECT_NAME=pmfe
PMF_PORT=8080
```

**Numa instalação existente, mantenha o nome de projecto Compose e o ficheiro `.env` utilizados anteriormente.** Mudar o nome do projecto selecciona outro volume e pode apresentar uma base vazia. O exemplo acima não deve substituir a configuração de uma instalação que já contém dados.

Se a porta 8080 estiver ocupada, verifique se existe outra instância da aplicação em execução. Para escolher outra porta, defina `PMF_PORT=8081` no `.env`, execute `docker compose up -d --wait` e aceda a http://localhost:8081.

O serviço é publicado apenas em `127.0.0.1`, para acesso no próprio computador.

## Contas e permissões

- Utilizadores autenticados podem consultar dados e documentos de acordo com as permissões das páginas.
- Administradores podem alterar dados, gerir turmas, publicar guias, efectuar eliminações autorizadas e criar cópias de segurança.
- A conta criada com `createsuperuser` tem acesso administrativo. Não é necessário recriá-la em cada actualização quando a mesma base é conservada.
- As permissões são verificadas no servidor.

## Importação de dados Excel

A importação utiliza o comando Django `import_excel`. O ficheiro deve respeitar uma das estruturas EPAL/PROÁGUA reconhecidas pelo importador.

Coloque o ficheiro na pasta do projecto com o nome `dados.xlsx` e copie-o para o backend:

```bash
docker compose cp dados.xlsx backend:/tmp/import.xlsx
```

Execute primeiro uma **simulação sem guardar os registos**:

```bash
docker compose exec backend python manage.py import_excel /tmp/import.xlsx --report /tmp/simulacao.json
docker compose exec backend python -m json.tool /tmp/simulacao.json
```

Reveja as alterações e os erros indicados. Crie uma cópia de segurança antes da importação efectiva, sobretudo se a base já contém dados. Para guardar os registos válidos:

```bash
docker compose exec backend python manage.py import_excel /tmp/import.xlsx --apply --report /data/resultado-importacao.json
```

Na importação integrada, o número mecanográfico identifica o técnico e o ID de participação permite reconhecer participações existentes. A reimportação conserva os resultados das participações integradas já guardadas. Dados provenientes de outro formato ou sistema de identificação exigem revisão antes da fusão.

Os caminhos `/tmp` e `/data` destes comandos pertencem ao contentor. Os ficheiros Excel de origem e os relatórios com dados pessoais devem permanecer fora do repositório público.

## Cópias de segurança e persistência

A base fica em `/data/pmf.sqlite3` dentro do backend. O volume `database` conserva os dados quando os contentores são substituídos ou removidos sem apagar o volume.

Para exportar uma cópia, entre como administrador e escolha **Cópia de segurança**:

- Sem relatórios arquivados, a aplicação fornece um ficheiro SQLite.
- Com relatórios arquivados, fornece um ZIP com a base e os PDFs. Conserve o ZIP completo.

No arranque com uma base existente, o script Docker guarda uma cópia da base, dos relatórios e da chave em `/data/backups/` antes das migrações. Estas cópias acumulam-se no mesmo volume; mantenha também uma cópia noutro suporte para protecção contra perda do disco.

Para restaurar, pare a aplicação, conserve uma cópia dos dados actuais e reponha a base e os respectivos relatórios no volume de dados. Os ficheiros devem continuar acessíveis ao utilizador do backend (`10001:10001`). Uma exportação SQL de SQLite deve ser restaurada com uma ferramenta compatível com SQLite.

**Não utilize `docker compose down -v` numa actualização normal: essa opção remove o volume de dados do projecto.**

## Actualização

Crie uma cópia de segurança, actualize o código e execute na pasta do projecto:

```bash
docker compose build
docker compose up -d --wait
```

Não é necessário executar `down` antes da construção. O comando `up` substitui os contentores que precisam de actualização. Mantenha o mesmo `.env` e o mesmo nome de projecto Compose para conservar a ligação à base existente.

Depois do arranque, actualize o navegador com **Ctrl+F5**. Não volte a importar o Excel para aplicar uma actualização de código: as migrações adaptam a base existente.

## Comandos úteis

```bash
# Consultar o estado dos contentores
docker compose ps

# Consultar as últimas mensagens dos serviços
docker compose logs --tail=100 backend frontend

# Parar e voltar a iniciar os mesmos contentores
docker compose stop
docker compose start

# Remover os contentores, conservando o volume de dados
docker compose down

# Abrir a consola Django
docker compose exec backend python manage.py shell
```

## Testes

Para executar os testes com as dependências da imagem backend, sem utilizar a base activa:

```bash
docker compose run --rm --no-deps -e PMF_DATA_DIR=/tmp/pmf-tests backend python manage.py test --noinput
```

Os testes abrangem regras de dados, permissões, revisão colectiva, planeamento, exportações, relatórios, assistente e eliminações protegidas. O teste opcional de leitura estrutural de PDF é ignorado quando `pypdf` não está disponível.

## Estrutura do repositório

```text
pmfe/
├── app/
│   ├── pmf/                 # Configuração Django e rotas
│   ├── training/            # Modelos, lógica, migrações e testes
│   │   └── resources/       # Modelo Excel de listas de presença
│   ├── templates/           # Páginas HTML
│   ├── static/              # CSS, JavaScript, fontes e imagens
│   ├── manage.py
│   └── requirements.txt
├── docker/
│   ├── nginx.conf
│   └── startup.py
├── backend.Dockerfile
├── frontend.Dockerfile
├── compose.yaml
├── .dockerignore
├── .gitignore
└── README.md
```

O `.gitignore` exclui ambientes locais, `.env`, segredos, bases, cópias de segurança, exportações SQL e ficheiros Excel com dados pessoais. O modelo `app/training/resources/attendance_template.xlsx` é conservado porque a aplicação depende dele.

Entre os documentos Markdown, apenas o `README.md` da raiz está previsto para publicação. Os restantes ficheiros `.md` ficam excluídos pelo `.gitignore`.

## Limitações actuais

- A configuração destina-se a um único computador e a uma única instância backend com SQLite.
- A avaliação de impacto no terreno/360° ainda não está activa. O arquivo privado de certificados/CV está disponível com o módulo documental e permissões adequadas.
- O assistente utiliza guias locais; não é uma IA generativa nem aprende automaticamente novas funcionalidades.
- Existem listas diárias de presença, mas o cálculo de horas utiliza os totais de dias de presença registados.
- A plataforma acompanha a entrega de certificados; a emissão automática a partir de um modelo ainda não está implementada.
- Os indicadores dependem da qualidade dos registos. Os dados EPAL não validados pelos RH continuam provisórios.
