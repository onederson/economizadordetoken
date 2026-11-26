import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMINHO_MAESTRO = os.path.join(RAIZ, "maestro.py")

FAKE_EXECUTAR = '''
def executar(j, execs, cat, a, passo=0):
    caminho = os.path.join(j["wt"], TAREFA)
    with open(caminho, encoding="utf-8") as fh:
        texto = fh.read()
    for linha in texto.splitlines():
        s = linha.strip()
        if s.startswith("DORME:"):
            time.sleep(float(s[6:].strip()))
        elif s.startswith("ESCREVA:"):
            parte = s[8:].strip()
            cam, conteudo = parte.split("=>", 1)
            dest = os.path.join(j["wt"], cam.strip())
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as out:
                out.write(conteudo.strip() + "\\n")
    return {"exec": execs[0], "sessao": "s1", "texto": "feito", "tin": 1, "tout": 1, "erro": None, "rc": 0, "estourou": False}
'''

CATALOGO_FALSO = """bolsos = ["anthropic"]
[rotas]
implementar = ["fake"]

[fake]
cli = "claude"
modelo = "x"
cota = "anthropic"
custo = "barato"
nivel = 2
"""


class TestSala(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.home = os.path.join(self.temp_dir, "home")
        os.makedirs(os.path.join(self.home, ".maestro"), exist_ok=True)
        self.env = dict(os.environ)
        self.env["HOME"] = self.home
        self.env["USERPROFILE"] = self.home
        for k in ("MAESTRO_EU", "MAESTRO_PROFUNDIDADE", "MAESTRO_GUARDA", "MAESTRO_LIMITE_AVISO", "MAESTRO_LIMITE_PARADA"):
            self.env.pop(k, None)

        self.patcher = unittest.mock.patch.dict(os.environ, {
            "HOME": self.home,
            "USERPROFILE": self.home,
        })
        self.patcher.start()
        for k in ("MAESTRO_EU", "MAESTRO_PROFUNDIDADE", "MAESTRO_GUARDA", "MAESTRO_LIMITE_AVISO", "MAESTRO_LIMITE_PARADA"):
            if k in os.environ:
                del os.environ[k]

        self.repo = os.path.join(self.temp_dir, "repo")
        os.makedirs(self.repo, exist_ok=True)
        self._git("init")
        self._git("config", "user.email", "test@test.com")
        self._git("config", "user.name", "test")
        self._escrever_repo("README.md", "inicial\n")
        self._git("add", ".")
        self._git("commit", "-m", "init")

        self.salas_criadas = []
        self.maestro_falso = None

    def tearDown(self):
        if self.maestro_falso and os.path.isfile(self.maestro_falso):
            for nome in self.salas_criadas:
                try:
                    subprocess.run(
                        [sys.executable, self.maestro_falso, "sala", "parar", nome],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=self.env,
                        timeout=30,
                    )
                except Exception:
                    pass
        self.patcher.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def importar_maestro(self):
        spec = importlib.util.spec_from_file_location("maestro_sala_test", CAMINHO_MAESTRO)
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        return modulo

    def _git(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )

    def _escrever_repo(self, rel, conteudo):
        caminho = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write(conteudo)
        return caminho

    def _escrever_arquivo(self, pasta, rel, conteudo):
        caminho = os.path.join(pasta, rel)
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        with open(caminho, "w", encoding="utf-8") as fh:
            fh.write(conteudo)
        return caminho

    def _preparar_executor_falso(self):
        if self.maestro_falso:
            return self.maestro_falso
        bin_dir = os.path.join(self.temp_dir, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        with open(CAMINHO_MAESTRO, encoding="utf-8") as fh:
            codigo = fh.read()
        alvo_olhar = "SALA_OLHAR = 10"
        self.assertIn(alvo_olhar, codigo)
        codigo = codigo.replace(alvo_olhar, "SALA_OLHAR = 1", 1)
        alvo_main = 'if __name__ == "__main__":'
        self.assertIn(alvo_main, codigo)
        codigo = codigo.replace(alvo_main, FAKE_EXECUTAR + "\n\n" + alvo_main, 1)
        self.maestro_falso = os.path.join(bin_dir, "maestro.py")
        with open(self.maestro_falso, "w", encoding="utf-8") as fh:
            fh.write(codigo)
        with open(os.path.join(bin_dir, "catalogo.toml"), "w", encoding="utf-8") as fh:
            fh.write(CATALOGO_FALSO)
        return self.maestro_falso

    def _sala(self, *args, timeout=120):
        maestro = self._preparar_executor_falso()
        return subprocess.run(
            [sys.executable, maestro, "sala", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self.env,
            timeout=timeout,
        )

    def _ler_sala_json(self, nome):
        caminho = os.path.join(self.home, ".maestro", "salas", nome, "sala.json")
        with open(caminho, encoding="utf-8") as fh:
            return json.load(fh)

    def test_01_glob_re_casa_area(self):
        m = self.importar_maestro()
        self.assertTrue(m.casa_area("a/x.txt", ["a/**"]))
        self.assertTrue(m.casa_area("a/b/c.txt", ["a/**"]))
        self.assertFalse(m.casa_area("ab/x.txt", ["a/**"]))
        self.assertTrue(m.casa_area("b/y.txt", ["b/"]))
        self.assertTrue(m.casa_area("scripts/x.gd", ["scripts/*.gd"]))
        self.assertFalse(m.casa_area("scripts/sub/x.gd", ["scripts/*.gd"]))
        self.assertTrue(m.casa_area("README.md", ["**/*.md"]))
        self.assertTrue(m.casa_area("docs/a.md", ["**/*.md"]))

    def test_02_areas_cruzam(self):
        m = self.importar_maestro()
        self.assertTrue(m.areas_cruzam(["a/**"], ["a/w.txt"]))
        self.assertFalse(m.areas_cruzam(["scripts/run/**"], ["scripts/melhorias/**"]))
        self.assertFalse(m.areas_cruzam(["x.gd"], ["y.gd"]))
        self.assertTrue(m.areas_cruzam(["x.gd"], ["x.gd"]))
        self.assertTrue(m.areas_cruzam([], ["a/**"]))
        self.assertTrue(m.areas_cruzam(["scripts/**"], ["scripts/run/*.gd"]))

    def test_03_ler_plano_recusa(self):
        m = self.importar_maestro()
        self._escrever_arquivo(self.repo, "t1.md", "tarefa\n")

        def ler(nome_arq, texto):
            arq = self._escrever_arquivo(self.repo, nome_arq, texto)
            _, _, erros, _ = m.ler_plano(arq)
            return erros

        erros = ler("p_dup.toml", 'nome = "pdup"\n[[frente]]\nid = "A"\ntarefa = "t1.md"\narea = ["a/**"]\n[[frente]]\nid = "A"\ntarefa = "t1.md"\narea = ["b/**"]\n')
        self.assertTrue(any("id repetido" in e for e in erros), erros)

        erros = ler("p_dep.toml", 'nome = "pdep"\n[[frente]]\nid = "A"\ntarefa = "t1.md"\narea = ["a/**"]\ndepois = ["Z"]\n')
        self.assertTrue(any("nao existe" in e for e in erros), erros)

        erros = ler("p_ciclo.toml", 'nome = "pciclo"\n[[frente]]\nid = "A"\ntarefa = "t1.md"\narea = ["a/**"]\ndepois = ["B"]\n[[frente]]\nid = "B"\ntarefa = "t1.md"\narea = ["b/**"]\ndepois = ["A"]\n')
        self.assertTrue(any("dependencia circular" in e for e in erros), erros)

        erros = ler("p_tar.toml", 'nome = "ptar"\n[[frente]]\nid = "A"\ntarefa = "nao_existe.md"\narea = ["a/**"]\n')
        self.assertTrue(any("arquivo de tarefa nao encontrado" in e for e in erros), erros)

        erros = ler("p_area.toml", 'nome = "parea"\n[[frente]]\nid = "A"\ntarefa = "t1.md"\narea = ["../fora/**"]\n')
        self.assertTrue(any(".." in e for e in erros), erros)

        erros = ler("p_juntar.toml", 'nome = "pjuntar"\njuntar = "talvez"\n[[frente]]\nid = "A"\ntarefa = "t1.md"\narea = ["a/**"]\n')
        self.assertTrue(any("juntar = 'talvez'" in e for e in erros), erros)

    def test_04_ler_plano_avisos(self):
        m = self.importar_maestro()
        self._escrever_arquivo(self.repo, "t1.md", "tarefa\n")
        plano = self._escrever_arquivo(
            self.repo,
            "p_avisos.toml",
            'nome = "pavisos"\nverificar = "python -c pass"\n'
            '[[frente]]\nid = "A"\ntarefa = "t1.md"\narea = ["a/**"]\n'
            '[[frente]]\nid = "B"\ntarefa = "t1.md"\narea = ["a/sub/**"]\n'
            '[[frente]]\nid = "C"\ntarefa = "t1.md"\n',
        )
        _, _, erros, avisos = m.ler_plano(plano)
        self.assertEqual(erros, [])
        self.assertTrue(any("sem 'area'" in av for av in avisos), avisos)
        self.assertTrue(any("se cruzam" in av for av in avisos), avisos)

    def test_05_motivo_espera(self):
        m = self.importar_maestro()
        frentes = {
            "A": {"id": "A", "depois": [], "area": ["a/**"], "estado": "espera"},
            "B": {"id": "B", "depois": ["A"], "area": ["b/**"], "estado": "espera"},
            "C": {"id": "C", "depois": [], "area": ["a/x.txt"], "estado": "espera"},
        }
        ordem = ["A", "B", "C"]
        self.assertIn("depois de A", m.motivo_espera(frentes["B"], frentes, ordem))
        self.assertIn("area cruza com A", m.motivo_espera(frentes["C"], frentes, ordem))

        frentes["A"]["estado"] = "juntada"
        self.assertIsNone(m.motivo_espera(frentes["B"], frentes, ordem))
        self.assertIsNone(m.motivo_espera(frentes["C"], frentes, ordem))

        frentes["A"]["estado"] = "descartada"
        self.assertIn("descartada", m.motivo_espera(frentes["B"], frentes, ordem))

    def test_06_sala_checar(self):
        self._escrever_arquivo(self.repo, "t.md", "tarefa\n")
        plano_ok = self._escrever_arquivo(
            self.repo,
            "checar_ok.toml",
            'nome = "checar_ok"\nparalelo = 2\n'
            '[[frente]]\nid = "A"\ntarefa = "t.md"\narea = ["a/**"]\n'
            '[[frente]]\nid = "B"\ntarefa = "t.md"\narea = ["b/"]\n'
            '[[frente]]\nid = "C"\ntarefa = "t.md"\narea = ["a/w.txt"]\n'
            '[[frente]]\nid = "D"\ntarefa = "t.md"\narea = ["d/**"]\ndepois = ["B"]\n'
            '[[frente]]\nid = "E"\ntarefa = "t.md"\narea = ["c/**"]\n',
        )
        r = self._sala("checar", plano_ok)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("onda 1: A", r.stdout)
        linha_onda1 = next(l for l in r.stdout.splitlines() if l.startswith("onda 1:"))
        self.assertIn("B [", linha_onda1)
        self.assertNotIn("C [", linha_onda1)

        plano_ciclo = self._escrever_arquivo(
            self.repo,
            "checar_ciclo.toml",
            'nome = "checar_ciclo"\n'
            '[[frente]]\nid = "A"\ntarefa = "t.md"\narea = ["a/**"]\ndepois = ["B"]\n'
            '[[frente]]\nid = "B"\ntarefa = "t.md"\narea = ["b/**"]\ndepois = ["A"]\n',
        )
        r_bad = self._sala("checar", plano_ciclo)
        self.assertEqual(r_bad.returncode, 1, r_bad.stdout + r_bad.stderr)

    def test_07_sala_rodar_auto(self):
        self._escrever_repo("comum.txt", "base\n")
        self._escrever_repo(
            "verif.py",
            "import os, sys\nsys.exit(1 if os.path.exists('c/ruim.txt') else 0)\n",
        )
        self._git("add", "comum.txt", "verif.py")
        self._git("commit", "-m", "setup sala07")

        plano_dir = os.path.join(self.temp_dir, "planos07")
        self._escrever_arquivo(plano_dir, "tA.md", "DORME: 2\nESCREVA: a/x.txt => arquivo a\n")
        self._escrever_arquivo(plano_dir, "tB.md", "ESCREVA: b/y.txt => arquivo b\n")
        self._escrever_arquivo(plano_dir, "tC.md", "ESCREVA: a/w.txt => arquivo c\n")
        self._escrever_arquivo(plano_dir, "tD.md", "ESCREVA: d/z.txt => arquivo d\nESCREVA: fora.txt => invasao\n")
        self._escrever_arquivo(plano_dir, "tE.md", "ESCREVA: c/ruim.txt => ruim\n")

        nome = "sala07"
        self.salas_criadas.append(nome)
        plano = self._escrever_arquivo(
            plano_dir,
            "plano.toml",
            f'nome = "{nome}"\n'
            f'dir = "{self.repo.replace(os.sep, "/")}"\n'
            'juntar = "auto"\n'
            'paralelo = 2\n'
            'protegidos = ["comum.txt"]\n'
            'verificar = "python verif.py"\n'
            'rodape = "Rodape-Sala07"\n'
            '[[frente]]\nid = "A"\ntarefa = "tA.md"\narea = ["a/**"]\n'
            '[[frente]]\nid = "B"\ntarefa = "tB.md"\narea = ["b/"]\n'
            '[[frente]]\nid = "C"\ntarefa = "tC.md"\narea = ["a/w.txt"]\n'
            '[[frente]]\nid = "D"\ntarefa = "tD.md"\narea = ["d/**"]\ndepois = ["B"]\n'
            '[[frente]]\nid = "E"\ntarefa = "tE.md"\narea = ["c/**"]\ntentativas = 0\n',
        )

        r = self._sala("rodar", plano, "--ate", "fim", timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

        s = self._ler_sala_json(nome)
        self.assertEqual(s["frentes"]["A"]["estado"], "juntada")
        self.assertEqual(s["frentes"]["B"]["estado"], "juntada")
        self.assertEqual(s["frentes"]["C"]["estado"], "juntada")
        self.assertEqual(s["frentes"]["D"]["estado"], "invadiu")
        self.assertIn("fora.txt", s["frentes"]["D"]["obs"])
        self.assertEqual(s["frentes"]["E"]["estado"], "falhou")

        commits = self._git("log", "--oneline").stdout.strip().splitlines()
        self.assertEqual(len(commits), 5)  # 2 iniciais + 3 novas frentes juntadas
        self.assertEqual(self._git("status", "--short").stdout.strip(), "")
        msg_c = self._git("log", "-1", "--format=%B", s["frentes"]["C"]["commit"]).stdout
        self.assertIn("Rodape-Sala07", msg_c)

    def test_08_protegido_invadiu(self):
        self._escrever_repo("comum.txt", "original\n")
        self._git("add", "comum.txt")
        self._git("commit", "-m", "add comum")

        plano_dir = os.path.join(self.temp_dir, "planos08")
        self._escrever_arquivo(plano_dir, "tP.md", "ESCREVA: comum.txt => alterado\n")
        nome = "sala08"
        self.salas_criadas.append(nome)
        plano = self._escrever_arquivo(
            plano_dir,
            "plano.toml",
            f'nome = "{nome}"\n'
            f'dir = "{self.repo.replace(os.sep, "/")}"\n'
            'juntar = "auto"\n'
            'protegidos = ["comum.txt"]\n'
            '[[frente]]\nid = "P"\ntarefa = "tP.md"\narea = ["**"]\n',
        )

        r = self._sala("rodar", plano, "--ate", "fim", timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = self._ler_sala_json(nome)
        self.assertEqual(s["frentes"]["P"]["estado"], "invadiu")
        self.assertIn("protegidos", s["frentes"]["P"]["obs"])

    def test_09_juntar_revisar_conflito(self):
        self._escrever_repo(
            "verif.py",
            "import os, sys\nsys.exit(1 if os.path.exists('x/a.txt') and os.path.exists('y/b.txt') else 0)\n",
        )
        self._git("add", "verif.py")
        self._git("commit", "-m", "add verif")

        plano_dir = os.path.join(self.temp_dir, "planos09")
        self._escrever_arquivo(plano_dir, "t1.md", "ESCREVA: x/a.txt => a\n")
        self._escrever_arquivo(plano_dir, "t2.md", "ESCREVA: y/b.txt => b\n")
        nome = "sala09"
        self.salas_criadas.append(nome)
        plano = self._escrever_arquivo(
            plano_dir,
            "plano.toml",
            f'nome = "{nome}"\n'
            f'dir = "{self.repo.replace(os.sep, "/")}"\n'
            'juntar = "revisar"\n'
            'verificar = "python verif.py"\n'
            '[[frente]]\nid = "F1"\ntarefa = "t1.md"\narea = ["x/**"]\n'
            '[[frente]]\nid = "F2"\ntarefa = "t2.md"\narea = ["y/**"]\n',
        )

        r = self._sala("rodar", plano, "--ate", "fim", timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        s = self._ler_sala_json(nome)
        self.assertEqual(s["frentes"]["F1"]["estado"], "pronta")
        self.assertEqual(s["frentes"]["F2"]["estado"], "pronta")

        antes = len(self._git("log", "--oneline").stdout.strip().splitlines())
        j1 = self._sala("juntar", nome, "F1")
        self.assertEqual(j1.returncode, 0, j1.stdout + j1.stderr)
        depois = len(self._git("log", "--oneline").stdout.strip().splitlines())
        self.assertEqual(depois, antes + 1)

        j2 = self._sala("juntar", nome, "F2")
        self.assertEqual(j2.returncode, 3, j2.stdout + j2.stderr)
        self.assertIn("desfeito", j2.stdout + j2.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "y", "b.txt")))
        self.assertEqual(self._git("status", "--short").stdout.strip(), "")
        s2 = self._ler_sala_json(nome)
        self.assertEqual(s2["frentes"]["F2"]["estado"], "conflito")

    def test_10_refazer_e_descartar(self):
        self._escrever_repo(
            "verif.py",
            "import os, sys\nsys.exit(1 if os.path.exists('x/a.txt') and os.path.exists('y/b.txt') else 0)\n",
        )
        self._git("add", "verif.py")
        self._git("commit", "-m", "add verif")

        plano_dir = os.path.join(self.temp_dir, "planos10")
        self._escrever_arquivo(plano_dir, "t1.md", "ESCREVA: x/a.txt => a\n")
        self._escrever_arquivo(plano_dir, "t2.md", "ESCREVA: y/b.txt => b\n")
        nome = "sala10"
        self.salas_criadas.append(nome)
        plano = self._escrever_arquivo(
            plano_dir,
            "plano.toml",
            f'nome = "{nome}"\n'
            f'dir = "{self.repo.replace(os.sep, "/")}"\n'
            'juntar = "revisar"\n'
            'verificar = "python verif.py"\n'
            '[[frente]]\nid = "F1"\ntarefa = "t1.md"\narea = ["x/**"]\n'
            '[[frente]]\nid = "F2"\ntarefa = "t2.md"\narea = ["y/**"]\n',
        )

        self.assertEqual(self._sala("rodar", plano, "--ate", "fim").returncode, 0)
        self.assertEqual(self._sala("juntar", nome, "F1").returncode, 0)
        self.assertEqual(self._sala("juntar", nome, "F2").returncode, 3)

        s = self._ler_sala_json(nome)
        job_f2 = s["frentes"]["F2"]["job"]
        with open(os.path.join(self.home, ".maestro", "jobs", job_f2, "job.json"), encoding="utf-8") as fh:
            wt_antigo = json.load(fh)["wt"]
        self.assertTrue(os.path.isdir(wt_antigo))

        r_ref = self._sala("refazer", nome, "F2")
        self.assertEqual(r_ref.returncode, 0, r_ref.stdout + r_ref.stderr)
        s_ref = self._ler_sala_json(nome)
        self.assertEqual(s_ref["frentes"]["F2"]["estado"], "espera")
        self.assertFalse(os.path.isdir(wt_antigo))

        r_desc = self._sala("descartar", nome, "F2")
        self.assertEqual(r_desc.returncode, 0, r_desc.stdout + r_desc.stderr)
        s_desc = self._ler_sala_json(nome)
        self.assertEqual(s_desc["frentes"]["F2"]["estado"], "descartada")

    def test_11_juntada_recusada_sujo(self):
        arq_repo = self._escrever_repo("x/a.txt", "original\n")
        self._git("add", "x/a.txt")
        self._git("commit", "-m", "add x/a")

        plano_dir = os.path.join(self.temp_dir, "planos11")
        self._escrever_arquivo(plano_dir, "t1.md", "ESCREVA: x/a.txt => frente\n")
        nome = "sala11"
        self.salas_criadas.append(nome)
        plano = self._escrever_arquivo(
            plano_dir,
            "plano.toml",
            f'nome = "{nome}"\n'
            f'dir = "{self.repo.replace(os.sep, "/")}"\n'
            'juntar = "revisar"\n'
            '[[frente]]\nid = "F1"\ntarefa = "t1.md"\narea = ["x/**"]\n',
        )

        self.assertEqual(self._sala("rodar", plano, "--ate", "fim").returncode, 0)
        with open(arq_repo, "w", encoding="utf-8") as fh:
            fh.write("local sujo\n")

        j = self._sala("juntar", nome, "F1")
        self.assertEqual(j.returncode, 3, j.stdout + j.stderr)
        self.assertIn("nao commitada", j.stdout + j.stderr)
        with open(arq_repo, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "local sujo\n")

    def test_12_plano_vivo(self):
        plano_dir = os.path.join(self.temp_dir, "planos12")
        self._escrever_arquivo(plano_dir, "t1.md", "ESCREVA: a/1.txt => um\n")
        self._escrever_arquivo(plano_dir, "t2.md", "ESCREVA: b/2.txt => dois\n")
        nome = "sala12"
        self.salas_criadas.append(nome)
        plano = self._escrever_arquivo(
            plano_dir,
            "plano.toml",
            f'nome = "{nome}"\n'
            f'dir = "{self.repo.replace(os.sep, "/")}"\n'
            'juntar = "auto"\n'
            '[[frente]]\nid = "F1"\ntarefa = "t1.md"\narea = ["a/**"]\n',
        )

        self.assertEqual(self._sala("rodar", plano, "--ate", "fim").returncode, 0)
        s1 = self._ler_sala_json(nome)
        job1 = s1["frentes"]["F1"]["job"]
        self.assertEqual(s1["frentes"]["F1"]["estado"], "juntada")

        with open(plano, "a", encoding="utf-8") as fh:
            fh.write('\n[[frente]]\nid = "F2"\ntarefa = "t2.md"\narea = ["b/**"]\n')

        self.assertEqual(self._sala("rodar", plano, "--ate", "fim").returncode, 0)
        s2 = self._ler_sala_json(nome)
        self.assertEqual(s2["frentes"]["F1"]["job"], job1)
        self.assertEqual(s2["frentes"]["F1"]["lancamentos"], 1)
        self.assertEqual(s2["frentes"]["F2"]["estado"], "juntada")

    def test_13_sala_parar(self):
        m = self.importar_maestro()
        maestro = self._preparar_executor_falso()
        plano_dir = os.path.join(self.temp_dir, "planos13")
        self._escrever_arquivo(plano_dir, "t1.md", "DORME: 300\nESCREVA: a/1.txt => oi\n")
        nome = "sala13"
        self.salas_criadas.append(nome)
        plano = self._escrever_arquivo(
            plano_dir,
            "plano.toml",
            f'nome = "{nome}"\n'
            f'dir = "{self.repo.replace(os.sep, "/")}"\n'
            'juntar = "revisar"\n'
            '[[frente]]\nid = "F1"\ntarefa = "t1.md"\narea = ["a/**"]\n',
        )

        p = subprocess.Popen(
            [sys.executable, maestro, "sala", "rodar", plano],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=self.env,
        )
        try:
            pid = None
            prazo = time.time() + 30
            while time.time() < prazo:
                try:
                    s = self._ler_sala_json(nome)
                    f = s["frentes"].get("F1")
                    if f and f.get("estado") == "rodando" and f.get("pid"):
                        pid = f["pid"]
                        break
                except (OSError, json.JSONDecodeError, KeyError):
                    pass
                time.sleep(0.2)
            self.assertIsNotNone(pid, "frente nao entrou em rodando com pid em 30s")
        finally:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=10)

        self.assertTrue(m.pid_vivo(pid))
        r_parar = self._sala("parar", nome)
        self.assertEqual(r_parar.returncode, 0, r_parar.stdout + r_parar.stderr)

        prazo_morte = time.time() + 10
        while time.time() < prazo_morte and m.pid_vivo(pid):
            time.sleep(0.2)
        self.assertFalse(m.pid_vivo(pid))
        s_final = self._ler_sala_json(nome)
        self.assertEqual(s_final["frentes"]["F1"]["estado"], "falhou")


if __name__ == "__main__":
    unittest.main()
