# PMFE — base consolidada 8088

Plataforma de Monitorização da Formação EPAL / PROÁGUA.

**Branch de trabalho:** `integracao/pmfe-8088-consolidada-20261118`.
Esta branch reúne a estrutura funcional e o design aprovado da 8088, o catálogo simplificado e os cálculos seleccionados do GitHub adaptados à estrutura existente. O `main` anterior permanece preservado. Para trabalhar nesta versão, ambos os colaboradores devem seleccionar esta branch.

## O que está incluído

- Dashboard Executivo com quatro cartões principais, H/D, distribuição de horas, participação por Direcção e Quadro de Excelência.
- Gestão de Formações: catálogo, grupos, calendário, inscrições, importação de assiduidade, validação de grupos, recursos, formadores e certificados.
- Fichas individuais, histórico, impressão/PDF e exportações Excel/PDF.
- Pesquisa de funcionários com sugestões, consultas sobrepostas, notificações e manual interno com exportação PDF.
- Avaliação, indicadores confirmados e 360° dentro de Gestão de Formações.
- Administração, permissões, auditoria e mecanismos de backup/recuperação.
- Login aquático, marca branca, efeitos de elevação e animação da torre antes da entrada.

A estrutura da 8088 foi conservada. As funções diárias por sessão/efectivo DRH exclusivas da antiga versão GitHub não foram copiadas integralmente: foram incorporadas as regras de cálculo compatíveis com os dados da 8088. Os serviços puros `git_kpi_service.py` e `git_certification.py` provêm do commit `8c126d7`, com adaptação em `operational_kpis.py`.

## Dados e compatibilidade

O Git contém código, recursos da aplicação e modelos vazios. **Não contém a base dos 1.510 funcionários, contas, palavras-passe, documentos privados, certificados pessoais ou os 100 formandos experimentais.** Uma instalação nova começa vazia; dados de produção são transferidos separadamente pelo responsável, sem os acrescentar ao Git. As imagens do manual usam dados fictícios de ensaio.

Esta branch usa a linhagem de migrações da 8088, diferente da antiga branch `main`. O arranque verifica a compatibilidade e recusa uma base de outra linhagem. Não copie o antigo ficheiro SQLite do GitHub para esta instalação nem utilize `migrate --fake` para contornar a verificação. Use uma pasta/volume novo. Uma transferência de dados entre linhagens exige um procedimento específico, não incluído nesta publicação.

Para uma cópia privada da própria base consolidada 8088, os dados e o ficheiro `secret.key` permanecem fora do Git e devem ser transferidos pelo responsável. Conserve uma cópia anterior antes de qualquer instalação sobre dados existentes.

## Instalação local — Python 3.12

No repositório, seleccionar a branch:

```sh
git fetch origin
git switch --track origin/integracao/pmfe-8088-consolidada-20261118
```

Criar um ambiente virtual e instalar as dependências. Em Windows/PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r app/requirements.txt
.\.venv\Scripts\python.exe app/manage.py migrate
.\.venv\Scripts\python.exe app/manage.py createsuperuser
.\.venv\Scripts\python.exe app/manage.py collectstatic --noinput
.\.venv\Scripts\python.exe scripts/run_local.py --port 8088
```

Abrir **http://127.0.0.1:8088/entrar/**. Se essa porta já estiver ocupada, usar `--port 8090` para uma instalação de ensaio.

Em Linux/macOS, os mesmos comandos usam `.venv/bin/python` em vez de `.\.venv\Scripts\python.exe`.

Por defeito, os dados ficam em `.runtime/pmfe-8088/`, fora do controlo Git. Não existe conta ou palavra-passe pré-definida. O administrador é criado pelo comando acima. A recuperação de palavra-passe mantém a orientação local: contactar o administrador.

## Docker

```sh
docker compose up --build -d
```

O endereço predefinido é **http://127.0.0.1:8088/**. O projecto Compose chama-se `pmfe-8088` e utiliza um volume próprio `database_8088`, separado do volume da versão anterior. `PMF_PORT` permite escolher outra porta. O arranque não cria utilizadores ou dados de demonstração.

Criar o administrador:

```sh
docker compose exec backend python manage.py createsuperuser
```

Os ficheiros do manual são servidos pelo backend. Fontes, imagens e scripts da aplicação estão incluídos para funcionamento sem serviços externos depois da instalação das dependências. Não iniciar estes comandos sobre o serviço operacional da porta 8080.

## Regras dos cálculos

- Horas frequentadas: presenças verificadas e duração conhecida, sem contar inscrições como presença.
- Assiduidade: horas de presença verificadas / horas previstas correspondentes; não é uma média simples de percentagens.
- H/D: horas verificadas importadas / 7. Dias efectivos são distintos e dependem de marcações diárias suficientes.
- Dados desconhecidos e zero confirmado permanecem distintos.
- Certificado obtido: indicação explícita Sim/Não. Não confirma automaticamente emissão nem entrega.
- Entregas históricas e declarações administrativas existentes são preservadas.
- Filtros mantêm a base temporal indicada no ecrã, incluindo o início da formação; esta publicação não introduz reconstrução mensal de estados passados nem durações diferentes por sessão dentro do mesmo grupo.

## Catálogo e eliminação

Formações disponíveis podem receber grupos sem confirmação documental prévia. Registos explicitamente marcados para revisão e formações arquivadas continuam protegidos. Criar um grupo/inscrição não cria automaticamente horas, notas ou certificados.

O catálogo apresenta Criar grupo, Ver grupos, Editar e Eliminar formação. A lista e o resumo do grupo apresentam Eliminar grupo quando permitido. A eliminação exige motivo e confirmação `ELIMINAR`, fica auditada e não permite apagar grupos com histórico nem formações com associações protegidas. O número do grupo eliminado não é reutilizado.

## Validação e trabalho conjunto

Foram aprovados 88 testes dos lotes funcionais. A publicação acrescenta três testes da protecção de linhagem, totalizando 91 verificações na suite seleccionada. Não se afirma que todos os testes históricos do repositório tenham sido actualizados ao design e às regras actuais.

```sh
python app/manage.py check
python app/manage.py makemigrations --check --dry-run
python app/manage.py collectstatic --noinput
python app/manage.py test training.test_release_lineage training.test_unified_kpis training.test_practical_catalogue training.test_planning training.test_scheduling_f6 training.test_attendance_management training.test_dashboard training.test_indicators training.test_certificate_register
```

Criar branches a partir desta base consolidada e rever os diffs antes de integrar. Preservar a estrutura/design aprovados e usar os cálculos partilhados, sem repetir fórmulas em templates ou exportações. `source-manifest.json` identifica os ficheiros publicados.

## Testes e satisfação — nova etapa

Disponível em Gestão de Formações → Avaliação → **Testes e satisfação**, e no resumo do grupo em Planeamento. Não altera o menu principal nem os quatro cartões do Dashboard.

A migração `0029` acrescenta apenas duas tabelas: escalas por grupo e resultados por inscrição. Não modifica notas históricas, presenças, horas, certificados ou utilizadores. Aplicar com `python app/manage.py migrate`, após backup da base de destino e confirmação da linhagem 8088; nunca usar `--fake` para uma base da antiga main.

- Introdução de pontos inicial/final, sem anexos; máximos independentes por teste e fixados após a primeira pontuação.
- Normalização a /20; evolução final menos inicial somente nos pares completos, incluindo zero e evolução negativa.
- Satisfação global de 1 a 5, com respostas/participações; ausências de resposta excluídas da média.
- Agregação por grupo ou formação sobre resultados individuais, sem média de médias de grupos.
- Correcções com motivo obrigatório e auditoria dos valores anteriores/posteriores; protecção contra gravações desactualizadas e duplicação da inscrição.
- Excel (Resumo/Resultados) e PDF calculados a partir da mesma função, abrangendo todo o filtro e não apenas a página visível.
- As menções são uma regra da plataforma, descrita no Manual, e não atribuem certificados. O circuito histórico de provas/tentativas continua separado e preservado.

Validação desta etapa: **116 testes locais aprovados**, incluindo 25 novos testes; gravação e descarga Excel/PDF verificadas no Edge, sem erros JavaScript nem extravasamento horizontal da página em ecrã móvel. A suite seleccionada da CI inclui agora `training.test_direct_assessments`; o resultado remoto da CI deve ser consultado no GitHub.

Esta branch é candidata a integração. A criação/publicação desta etapa não activa a migração nas bases locais 8088 ou 8080.

## Correcção de visibilidade dos cartões

Os quatro valores de Colaboradores & Formação eram calculados e renderizados correctamente, mas a regra legada `.gf-kpi b { color: #fff }` deixava-os brancos sobre o cartão branco do tema aprovado. A cor dos valores foi corrigida no tema claro, mantendo dimensões, fontes, layout e efeitos. A referência do CSS recebeu uma nova versão para renovar a cache do navegador.

As consultas e regras de cálculo não foram alteradas. A suite seleccionada passa a incluir `training.test_home_card_values`: **124 testes locais aprovados**, incluindo oito testes de valores sem anexos, universo de 1.510 sem histórico, contagem distinta por pessoa, filtros e registos legados/inactivos. A verificação visual da instalação local é registada separadamente, sem publicar capturas com dados pessoais.
