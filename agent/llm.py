import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

from config import (
    BACKEND,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_HTTP_TIMEOUT,
    LLM_MAX_RETRIES,
    LLM_MODEL,
    LLM_REPETITION_PENALTY,
    MODEL_FALLBACK,
    THINKING_BUDGET_FORWARD,
    THINKING_MAX_TOKENS,
)
from openai import APIConnectionError, APITimeoutError, OpenAI
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.rule import Rule
from rich.spinner import Spinner
from rich.text import Text

from agent.tokens import count_tokens

logger = logging.getLogger(__name__)
console = Console()

SYSTEM_PROMPT = """\
Tu es Klody, un agent de coding expert. Réponds en français.

RÈGLE CRITIQUE : N'utilise les outils QUE si la tâche l'exige explicitement. \
Pour les questions générales, la conversation, ou les explications : \
réponds DIRECTEMENT sans outil.

Quand tu dois agir sur le code :
1. Lis les fichiers concernés avant de les modifier
2. Exécute étape par étape
3. Vérifie chaque action
4. Rends compte clairement

Apprentissage des pratiques utilisateur :
- Si l'utilisateur te demande d'analyser ses exports LLM, utilise list_imports \
puis import_llm_export pour lire et analyser chaque fichier.
- Après analyse, utilise save_skill pour mémoriser les patterns importants \
(langages préférés, frameworks, habitudes de code, questions récurrentes).
- Enrichis ta compréhension de l'utilisateur à chaque import.

Dépôts GitHub et bonnes pratiques :
- Tu peux lire n'importe quel dépôt GitHub avec browse_repo et read_github_file.
- Utilise extract_best_practices pour analyser un dépôt et identifier ses patterns.
- Après analyse, utilise save_skill pour mémoriser les bonnes pratiques utiles.
- Utilise index_github_repo pour ajouter un dépôt à LibraryBrain (recherche RAG).
- Pour travailler sur du code : clone_github_repo le clone et l'ouvre dans PyCharm.
- Pour créer un projet inspiré d'un dépôt : extract_best_practices → create_project → \
adapte avec write_file en lisant le code source via read_github_file.

Aperçu de code web (preview_code) :
- Quand tu génères du HTML/CSS/JS, utilise preview_code pour créer un aperçu \
local et ouvrir automatiquement le navigateur.
- Sépare proprement : le HTML du body dans html, le CSS dans css, le JS dans js. \
Ne place JAMAIS un document HTML complet imbriqué dans un autre — html attend le \
contenu du body, pas un second <!DOCTYPE>/<html>/<head>.
- DÉPENDANCES EXTERNES : si ton JS utilise une librairie (Three.js, Chart.js, d3, \
p5, GSAP…), tu DOIS fournir son URL CDN dans le paramètre scripts (liste). \
Exemple Three.js : scripts=["https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"]. \
Sans cela, la variable globale (THREE, Chart…) est indéfinie et la page reste vide.
- Pour un <canvas>/WebGL plein écran, ajoute le CSS `body{margin:0} canvas{display:block}`.
- VISUALISATIONS 3D (Three.js) — vise un rendu présentable, pas un brouillon :
  * Caméra en angle, jamais frontale — ex. camera.position.set(15, 12, 18); camera.lookAt(0,0,0).
  * Contrôles : ajoute TOUJOURS new THREE.OrbitControls(camera, renderer.domElement) \
pour que l'utilisateur puisse tourner et zoomer la scène à la souris.
  * Éclairage : combine HemisphereLight(0xffffff, 0x444444, 0.6) + DirectionalLight(0xffffff, 0.8) \
posée en (5,10,7). Évite AmbientLight(0x404040) seul — la scène devient noire.
  * Fond de scène : scene.background = new THREE.Color(0x87ceeb) (ciel) ou similaire — pas le noir par défaut.
  * Couleurs DISTINCTES par élément (sol vert, murs beige/brique, toit rouge sombre, etc.) — \
ne mets pas la même couleur partout sinon les volumes se confondent.
  * Pyramide à 4 pans (toit, par ex.) : CylinderGeometry(0, baseRadius, height, 4) — \
ne fais pas rotation.x = Math.PI/4 sur un ConeGeometry, le toit finit couché.
  * Boucle d'animation : appelle controls.update() avant renderer.render() si OrbitControls(damping).
- AUTO-CORRECTION : la valeur de retour de preview_code peut contenir une section \
"⚠ Avertissements". Lis-la systématiquement. Si elle signale une lib manquante ou \
un problème, corrige ton appel (ajoute les scripts, sépare le HTML) et rappelle \
preview_code — ne déclare jamais l'aperçu réussi tant qu'il reste des avertissements.
- Utilise preview_file pour ouvrir un fichier .html existant dans le navigateur.
- list_previews affiche tous les aperçus disponibles avec leurs URLs.

Apprentissage continu :
- Utilise learn_from_books pour acquérir des connaissances depuis LibraryBrain \
et les sauvegarder comme compétences permanentes.
- Quand tu rencontres un sujet technique où tu manques de profondeur, \
propose d'apprendre via les livres indexés.
- Après avoir appris, adapte tes réponses en utilisant ces nouvelles connaissances.

Proactivité :
- Tu as accès au profil de l'utilisateur (technologies préférées, activités récurrentes).
- Sois force de proposition : suggère des améliorations, des outils, des patterns \
adaptés à la stack et aux habitudes détectées.
- Anticipe les besoins : si l'utilisateur fait souvent X suivi de Y, propose Y en avance.
- Utilise remember_fact pour mémoriser les préférences découvertes.
- Si l'utilisateur évoque un fait, une décision ou un travail passé ABSENT du contexte \
(« tu te souviens de… », « qu'avait-on décidé pour… »), utilise rappeler_memoire : \
recherche sémantique dans TOUTE la mémoire archivée (faits anciens + sessions passées), \
au-delà des faits récents affichés ci-dessus.
- Si l'utilisateur demande de dire, lire ou annoncer quelque chose à voix haute, \
utilise speak (parole courte, haut-parleurs). Pour CHANTER ou créer une chanson, \
utilise mcp__vocalbrain__generer_chanson puis suis avec statut_generation.

Ne modifie jamais un fichier sans l'avoir lu. \
Avant toute commande bash, explique pourquoi.\
"""


def _has_markdown(text: str) -> bool:
    """Détecte si le texte contient du Markdown significatif."""
    markers = ("```", "**", "##", "# ", "- ", "* ", "> ", "| ")
    return any(m in text for m in markers)


def _build_xml_tool_call(
    fn_name: str,
    body: str,
    valid_tool_names: set[str],
    lenient: bool = False,
) -> dict | None:
    """Construit un tool_call OpenAI à partir d'un body XML `<parameter=...>…`.

    Args:
        fn_name : nom de la fonction (ex: 'preview_code')
        body : contenu entre `<function=…>` et `</function>` (ou tout ce qui suit
               si le tag fermant manque).
        valid_tool_names : set des noms valides — sinon None.
        lenient : si True, accepte aussi les `<parameter=name>` non fermés en
                  prenant le contenu jusqu'au prochain `<parameter=` OU la fin
                  du body. Utile quand le LLM a tronqué son output.

    Returns:
        Le dict tool_call OpenAI-shape, ou None si aucun paramètre extractible
        ou nom invalide.
    """
    if fn_name not in valid_tool_names:
        return None

    args: dict = {}
    # 1) Paramètres bien fermés. Regex « tempérée » : la valeur ne peut pas
    # enjamber l'ouverture du paramètre suivant. Sans ça, un paramètre NON fermé
    # (le modèle a oublié </parameter>) avalait tout jusqu'au </parameter> du
    # paramètre SUIVANT : '<parameter=js>' fuyait dans la valeur de html et js
    # disparaissait des args (vécu 03/07 : 11 previews « canard 3D » émises sans
    # code). Le paramètre non fermé est ramassé proprement par la passe lenient.
    # Contrepartie assumée : une valeur contenant LITTÉRALEMENT '<parameter='
    # est coupée là — le format n'a aucun échappement pour ce cas.
    for m in re.finditer(
        r'<parameter=(\w+)>((?:(?!<parameter=)[\s\S])*?)</parameter>',
        body,
        re.IGNORECASE,
    ):
        args[m.group(1)] = m.group(2).strip()

    # 2) Mode lenient : ramasser les paramètres ouverts mais non fermés
    if lenient:
        # Split par <parameter=name>, garde le contenu jusqu'au prochain
        # <parameter= ou jusqu'à la fin (peut être tronqué en plein milieu).
        for m in re.finditer(r'<parameter=(\w+)>', body, re.IGNORECASE):
            name = m.group(1)
            if name in args:
                continue  # déjà extrait via la passe stricte
            start = m.end()
            # Cherche le prochain <parameter= après start, ou fin du body
            next_p = re.search(r'<parameter=\w+>|</parameter>', body[start:], re.IGNORECASE)
            end = start + next_p.start() if next_p else len(body)
            value = body[start:end].strip()
            if value:
                args[name] = value

    if not args:
        return None
    return {
        "id": f"call_{uuid.uuid4().hex[:8]}",
        "type": "function",
        "function": {
            "name": fn_name,
            "arguments": json.dumps(args),
        },
    }


class LLMClient:
    def __init__(self, model: str = LLM_MODEL):
        self.model = model
        self.client = OpenAI(
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
            timeout=LLM_HTTP_TIMEOUT,
            max_retries=LLM_MAX_RETRIES,
        )
        self._backend = BACKEND
        # Compteur de tokens approximatif (session courante)
        self.total_tokens: int = 0

    def switch_to(self, model: str, base_url: str, api_key: str) -> None:
        """Bascule le client sur un autre modèle/endpoint (ex: modèle code dédié).

        Mutation EN PLACE (on ne remplace pas l'objet LLMClient) pour que les
        détenteurs d'une référence — notamment Best-of-N — voient le changement.
        No-op si le modèle est déjà actif (évite de recréer le client OpenAI).
        """
        if model == self.model:
            return
        self.model = model
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=LLM_HTTP_TIMEOUT,
            max_retries=LLM_MAX_RETRIES,
        )
        logger.info("LLM basculé sur le modèle '%s' (%s)", model, base_url)

    def stream_chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        token_callback: Callable[[str], None] | None = None,
        temperature: float = 0.1,
        silent: bool = False,
        tool_choice: str = "auto",
        max_tokens: int = 8192,
        enable_thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> tuple[str, list[dict] | None]:
        """
        Envoie les messages et streame la réponse avec :
        - Spinner "Klody réfléchit..." avant le premier token
        - Rendu Markdown progressif pendant le streaming
        - Fallback : parse les tool calls émis en JSON texte

        Args:
            temperature : 0.0-1.0. Plus haut = plus de diversité (utile pour Best-of-N).
            silent : si True, supprime tout affichage console (utile pour Best-of-N
                     où on ne veut afficher que le candidat retenu).
            tool_choice : "auto" (défaut) | "required" (force un tool_call) | "none".
                          "required" est utilisé par l'anti-stall pour forcer une action
                          quand le LLM répond en texte pur sur une tâche d'action.
            enable_thinking : active le mode raisonnement (Qwen3 brain). Le modèle
                     émet alors un CoT (champ `delta.reasoning`, capté et affiché en
                     filigrane) AVANT le `content`. On élargit max_tokens en
                     conséquence. No-op sur un modèle sans thinking (le serveur
                     ignore le kwarg). Cf. config.THINKING_*.
            thinking_budget : entier PAR TYPE DE TÂCHE, forwardé dans
                     chat_template_kwargs.thinking_budget (FORWARD-COMPAT). NO-OP sur
                     les templates actuels (Qwen3.6 ignore la clé — vérifié : l'appel
                     live passe sans erreur). On NE module PAS max_tokens avec : le
                     plafond ne sait qu'ÉLARGIR (`max()`), pas réduire, donc moduler
                     par là serait un no-op (le défaut 8192 ≥ tous les tiers). Borner
                     le CoT seul exigerait une troncature dure du flux (écartée : risque
                     sur le format tool-call). Cf. docs/thinking-budget-policy.md et
                     Orchestrator._thinking_budget. None ⇒ clé absente (forme historique).
        """
        if enable_thinking:
            # Le CoT précède la réponse et consomme beaucoup de tokens : on élargit le
            # plafond global pour qu'il ne mange pas toute la réponse (comportement
            # historique INCHANGÉ). Le budget par-tâche ne touche PAS max_tokens.
            max_tokens = max(max_tokens, THINKING_MAX_TOKENS)
        params: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,  # défaut généreux : 8192 tokens pour gros codes Three.js
        }
        extra_body: dict = {}
        if LLM_REPETITION_PENALTY > 1.0:
            # Param mlx_lm (hors spec OpenAI) → extra_body obligatoire, sinon le
            # SDK openai rejette le kwarg. Cf. config.LLM_REPETITION_PENALTY.
            extra_body["repetition_penalty"] = LLM_REPETITION_PENALTY
        if enable_thinking:
            ctk: dict = {"enable_thinking": True}
            # Forward-compat : on n'ajoute thinking_budget QUE s'il est fourni et que
            # le forward est activé → sans budget, la forme reste {"enable_thinking":
            # True} (contrat historique préservé). Le template Qwen3.6 ignore la clé
            # aujourd'hui (NO-OP) ; le plafond réel est appliqué via max_tokens.
            if thinking_budget is not None and THINKING_BUDGET_FORWARD:
                ctk["thinking_budget"] = thinking_budget
            extra_body["chat_template_kwargs"] = ctk
        if extra_body:
            params["extra_body"] = extra_body
        if tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice

        full_content = ""
        reasoning_buf = ""  # CoT (mode thinking) — capté, jamais réinjecté dans l'historique
        raw_tool_calls: dict[int, dict] = {}
        t0 = time.monotonic()

        try:
            stream = self.client.chat.completions.create(**params)

            if silent:
                # Mode silencieux : on consomme le stream sans affichage console
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    reasoning_buf += self._delta_reasoning(delta)
                    if delta.content:
                        full_content += delta.content
                        if token_callback:
                            token_callback(delta.content)
                    if delta.tool_calls:
                        for tc_chunk in delta.tool_calls:
                            self._accumulate_tool_call(raw_tool_calls, tc_chunk)
            else:
                # Phase 1 : spinner pendant que le modèle charge (ou raisonne)
                spinner = Spinner("dots2", text=Text(" Klody réfléchit…", style="dim cyan"))

                with Live(spinner, console=console, refresh_per_second=12, transient=True):
                    for chunk in stream:
                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta

                        # Le CoT (thinking) précède le content : on l'accumule sans
                        # rompre le spinner — la réponse n'a pas encore commencé.
                        reasoning_buf += self._delta_reasoning(delta)

                        if delta.content:
                            full_content += delta.content
                            if token_callback:
                                token_callback(delta.content)
                            break

                        if delta.tool_calls:
                            for tc_chunk in delta.tool_calls:
                                self._accumulate_tool_call(raw_tool_calls, tc_chunk)
                            break

                # Phase 2 : accumulation des tokens (spinner déjà fermé)
                for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    reasoning_buf += self._delta_reasoning(delta)

                    if delta.content:
                        full_content += delta.content
                        if token_callback:
                            token_callback(delta.content)

                    if delta.tool_calls:
                        for tc_chunk in delta.tool_calls:
                            self._accumulate_tool_call(raw_tool_calls, tc_chunk)

            elapsed = time.monotonic() - t0
            out_tokens = count_tokens(full_content)
            if reasoning_buf:
                logger.info("Raisonnement (CoT): %d chars", len(reasoning_buf))

            # Rendu final : Markdown si détecté, sinon texte brut (uniquement si non-silent)
            if full_content and not silent:
                if _has_markdown(full_content):
                    console.print(Markdown(full_content))
                else:
                    console.print(full_content, markup=False, highlight=False)
                think_note = (
                    f" · 🧠 ~{count_tokens(reasoning_buf)} tok raisonnés"
                    if reasoning_buf else ""
                )
                console.print(Rule(
                    f"[dim]⏱ {elapsed:.1f}s · ~{out_tokens} tokens{think_note}[/dim]",
                    style="dim blue",
                ))
            elif reasoning_buf and not full_content and not silent:
                # Le CoT a consommé tout le budget sans produire de réponse.
                console.print(
                    "[dim yellow]  ⚠  Raisonnement interrompu avant la réponse "
                    "(budget de tokens). Augmente THINKING_MAX_TOKENS.[/dim yellow]"
                )

            # Compteur de tokens (réponse uniquement — l'entrée n'est comptée qu'une fois à l'envoi)
            self.total_tokens += out_tokens

            tool_calls = list(raw_tool_calls.values()) if raw_tool_calls else None

            # Fallback : tool call émis en JSON texte ou contenu mixte (texte + JSON)
            # extract_mixed_tool_call gère : JSON pur, format compact `tool [p] {…}`,
            # et texte libre suivi d'un JSON tool call.
            if not tool_calls and full_content and tools:
                valid_names = {t["function"]["name"] for t in tools}
                full_content, tool_calls = self.extract_mixed_tool_call(
                    full_content, valid_names
                )

            if full_content:
                logger.info("Réponse LLM: %d chars", len(full_content))
            if tool_calls:
                logger.info("Tool calls: %s", [tc["function"]["name"] for tc in tool_calls])

            return full_content, tool_calls

        except APIConnectionError as e:
            logger.error("Ollama inaccessible: %s", e)
            console.print(
                "\n[bold red]✗ Impossible de joindre Ollama.[/bold red]\n"
                "[dim]  → ollama serve[/dim]\n"
            )
            raise
        except APITimeoutError as e:
            logger.error("Timeout LLM: %s", e)
            # Bascule automatique sur le modèle de secours si disponible
            if self.model != MODEL_FALLBACK:
                logger.warning("Timeout — bascule sur '%s'", MODEL_FALLBACK)
                console.print(
                    f"\n[yellow]⚠  Timeout — bascule automatique sur [bold]{MODEL_FALLBACK}[/bold][/yellow]\n"
                )
                self.model = MODEL_FALLBACK
                return self.stream_chat(messages, tools, token_callback)
            console.print("\n[bold red]✗ Timeout du modèle.[/bold red]\n")
            raise
        except Exception as e:
            err_str = str(e).lower()
            # Modèle introuvable → bascule sur le modèle de secours
            if ("not found" in err_str or "does not exist" in err_str) and self.model != MODEL_FALLBACK:
                logger.warning("Modèle '%s' introuvable — bascule sur '%s'", self.model, MODEL_FALLBACK)
                console.print(
                    f"\n[yellow]⚠  Modèle [bold]{self.model}[/bold] introuvable — "
                    f"bascule sur [bold]{MODEL_FALLBACK}[/bold][/yellow]\n"
                )
                self.model = MODEL_FALLBACK
                return self.stream_chat(messages, tools, token_callback)
            logger.error("Erreur LLM: %s", e)
            raise

    # ------------------------------------------------------------------ #
    # Helpers                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _delta_reasoning(delta: Any) -> str:
        """Texte de raisonnement (CoT) d'un delta de stream, quand le mode thinking
        est actif. Les modèles Qwen3 brain l'exposent via `delta.reasoning` ;
        certains builds ne le posent que dans `model_extra`. Retourne '' si absent —
        donc no-op total sur un modèle sans thinking."""
        r = getattr(delta, "reasoning", None)
        if not r:
            em = getattr(delta, "model_extra", None) or {}
            r = em.get("reasoning") or em.get("reasoning_content")
        return r or ""

    def _accumulate_tool_call(self, raw: dict, tc_chunk: Any) -> None:
        idx = tc_chunk.index
        if idx not in raw:
            raw[idx] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
        if tc_chunk.id:
            raw[idx]["id"] += tc_chunk.id
        if tc_chunk.function:
            if tc_chunk.function.name:
                raw[idx]["function"]["name"] += tc_chunk.function.name
            if tc_chunk.function.arguments:
                raw[idx]["function"]["arguments"] += tc_chunk.function.arguments

    @staticmethod
    def _repair_json_quotes(text: str) -> str:
        """Tente de réparer un JSON cassé par des guillemets non-échappés
        à l'intérieur de valeurs string (ex: docstrings Python `\"\"\"`).

        Stratégies essayées dans l'ordre :
        1. Remplacer `\"\"\"` (triple-quote littéral non-échappé) par `\\\"\\\"\\\"`.
        2. Remplacer les `\\n` littéraux (backslash-n en deux chars) par le vrai \\n.
        """
        # Stratégie 1 : triple-quote non-échappé → échappé
        # Cherche `"""` non précédé d'un backslash dans le texte JSON brut.
        repaired = re.sub(r'(?<!\\)"""', r'\\"\\"\\"', text)
        if repaired != text:
            return repaired
        # Stratégie 2 : guillemets doubles non-échappés isolés dans une valeur
        # (séquence `"` qui n'ouvre/ferme pas une clé/valeur JSON)
        # Approche naïve : remplacer tous les `"` non précédés de `\` à l'intérieur
        # des strings, en excluant la structure JSON de base.
        return text

    def _parse_text_tool_calls(
        self, content: str, valid_tool_names: set[str]
    ) -> list[dict] | None:
        """
        Fallback : parse les tool calls émis comme JSON texte.
        Gère objet unique, liste, blocs ```json```,
        ET le format compact `tool_name [param] {"key": "val"}` émis par
        certains modèles (ex: qwen2.5-coder, qwen3.5).
        """
        text = content.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
        text = text.strip()

        # --- Format XML-like : `<function=tool_name><parameter=p>val</parameter></function>` ---
        # Utilisé par Qwen3-Coder dans certains contextes.
        # On exige que le texte commence par `<function=` (ignorant les whitespaces)
        # pour ne pas voler le travail d'extract_mixed_tool_call sur du contenu mixte.
        if text.startswith("<function="):
            # Cherche d'abord les XML calls bien fermés
            xml_calls = list(re.finditer(
                r'<function=(\w+)>([\s\S]*?)</function>', text, re.IGNORECASE,
            ))
            results: list[dict] = []
            for m in xml_calls:
                # lenient=True même sur un bloc <function> bien fermé : un
                # </parameter> peut manquer À L'INTÉRIEUR (vécu 03/07). La passe
                # stricte tempérée ignore ce paramètre ; la passe lenient le
                # récupère (valeur jusqu'au paramètre suivant) au lieu de le perdre.
                tc = _build_xml_tool_call(m.group(1), m.group(2), valid_tool_names, lenient=True)
                if tc:
                    results.append(tc)

            # Cas robuste : le LLM a coupé son output avant </function> (truncation
            # streaming, max_tokens, BoN température haute…). Si on n'a aucun call
            # bien formé MAIS le texte commence par <function=name>, on tente quand
            # même un best-effort sur la fonction principale en récupérant les
            # paramètres présents (ouverts comme fermés).
            if not results:
                head = re.match(r'<function=(\w+)>', text, re.IGNORECASE)
                if head:
                    fn_name = head.group(1)
                    body = text[head.end():]
                    # Supprime un éventuel </function> traînant
                    body = re.sub(r'</function>\s*$', '', body)
                    tc = _build_xml_tool_call(fn_name, body, valid_tool_names, lenient=True)
                    if tc:
                        results.append(tc)

            if results:
                return results

        # --- Format compact : `tool_name [param] {"key": "val"}` ---
        # Exemple : `read_file [path] {"path":"app.py"}`
        # Exemple : `write_file [path, content] {"path":"x.py","content":"..."}`
        if not text.startswith(("{", "[")):
            names_pattern = "|".join(re.escape(n) for n in valid_tool_names)
            compact = re.match(
                rf'^({names_pattern})\s*(?:\[[^\]]*\])?\s*(\{{.*}})$',
                text,
                re.DOTALL,
            )
            if compact:
                name, args_str = compact.group(1), compact.group(2)
                try:
                    args = json.loads(args_str)
                    return [{
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(args),
                        },
                    }]
                except json.JSONDecodeError:
                    pass
            return None

        # --- Format JSON standard ---
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Tentative de réparation : certains modèles émettent des `"""`
            # non-échappés dans les valeurs JSON (ex: docstrings Python).
            # On cherche et remplace les guillemets internes non-échappés.
            repaired = self._repair_json_quotes(text)
            try:
                data = json.loads(repaired)
            except json.JSONDecodeError:
                return None

        def make_call(item: dict) -> dict | None:
            name = item.get("name", "")
            if name not in valid_tool_names:
                return None
            args = item.get("arguments", item.get("parameters", {}))
            return {
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                },
            }

        if isinstance(data, dict):
            call = make_call(data)
            return [call] if call else None

        if isinstance(data, list):
            calls = [c for item in data if isinstance(item, dict) for c in [make_call(item)] if c]
            return calls if calls else None

        return None

    def extract_mixed_tool_call(
        self, content: str, valid_tool_names: set[str]
    ) -> tuple[str, list[dict] | None]:
        """
        Extrait un tool call depuis un contenu mixte (texte + appel collé).
        Gère JSON (`{"name":...}`), XML-like (`<function=...>`) et format compact.
        Retourne (texte_avant, tool_calls) ou (content, None) si rien trouvé.
        """
        # Essai pure (JSON, XML, compact) d'abord
        pure = self._parse_text_tool_calls(content, valid_tool_names)
        if pure:
            return "", pure

        # --- XML-like dans contenu mixte ---
        xml_match = re.search(r'<function=\w+>[\s\S]*?</function>', content, re.IGNORECASE)
        if xml_match:
            text_part = content[:xml_match.start()].rstrip()
            xml_part = content[xml_match.start():]
            parsed = self._parse_text_tool_calls(xml_part, valid_tool_names)
            if parsed:
                return text_part, parsed

        # --- JSON {"name":...} dans contenu mixte ---
        # Cherche TOUS les objets JSON tool call dans le contenu (un modèle peut
        # en émettre plusieurs collés sans liste `[]`, ex: `{...} {...}`).
        names_pattern = "|".join(re.escape(n) for n in valid_tool_names)
        pattern = rf'\{{"name":\s*"(?:{names_pattern})"'
        matches = list(re.finditer(pattern, content))
        if not matches:
            return content, None

        # Texte avant le PREMIER appel
        text_part = content[:matches[0].start()].rstrip()

        # Extraire chaque appel individuellement via décodage glouton
        decoder = json.JSONDecoder()
        all_calls: list[dict] = []
        for m in matches:
            start = m.start()
            try:
                obj, _end = decoder.raw_decode(content[start:])
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            name = obj.get("name", "")
            if name not in valid_tool_names:
                continue
            args = obj.get("arguments", obj.get("parameters", {}))
            all_calls.append({
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(args) if isinstance(args, dict) else str(args),
                },
            })

        if all_calls:
            return text_part, all_calls

        return content, None
