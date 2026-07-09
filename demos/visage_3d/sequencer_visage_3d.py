#!/usr/bin/env python3
"""
Séquençage de Visage Humain en 3D — Pipeline Complet
=====================================================

Pipeline :
1. Capture webcam → MediaPipe Face Mesh (468 points 3D)
2. Mapping des landmarks vers un mesh Blender réaliste (Human Head)
3. Génération d'animation keyframe par keyframe

Dépendances :
- mediapipe (déjà présent)
- opencv-python (déjà présent)
- bpy (API Blender — fourni automatiquement si Blender est installé)

Utilisation :
  python sequencer_visage_3d.py --capture    # Capture en temps réel + export .blend
  python sequencer_visage_3d.py --playback   # Replay depuis un fichier JSON
  python sequencer_visage_3d.py --mesh       # Génère juste le mesh statique
"""

import cv2
import numpy as np
import json
import os
import sys
import time
import argparse

# ============================================================================
# GARDE-FOU CAPTURE — pas de données factices silencieuses
# ============================================================================


class WebcamUnavailable(RuntimeError):
    """La webcam n'a pas pu fournir d'images. Cas le plus fréquent ici :
    permission caméra macOS refusée car le process tourne en DAEMON/background
    (macOS TCC n'accorde la caméra qu'à une app GUI au 1er plan). On LÈVE au lieu
    d'inventer des landmarks : un échec CLAIR vaut mieux qu'un faux visage muet."""


def _camera_context_hint() -> str:
    daemon = not sys.stdin.isatty()
    base = (
        "→ Lance la capture dans un VRAI terminal GUI (Terminal.app / iTerm), PAS "
        "via le daemon Klody : au 1er lancement macOS demande l'accès caméra — "
        "autorise-le (Réglages > Confidentialité et sécurité > Caméra)."
    )
    if daemon:
        base = ("Contexte NON-interactif détecté (daemon/API) — la caméra y est "
                "quasi toujours bloquée par macOS.\n") + base
    return base


def _pick_working_camera(max_idx: int = 3, warmup_s: float = 1.2):
    """Renvoie l'index de la 1re caméra qui délivre des images NON-NOIRES.

    Sur ce Mac il y a plusieurs devices (built-in + iPhone Continuity) : selon
    l'ordre, index 0 peut être une caméra inactive qui renvoie du noir tandis
    qu'un autre index donne le vrai flux. On scanne et on choisit le bon."""
    for idx in range(max_idx + 1):
        cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            cap.release()
            continue
        ok = False
        t0 = time.time()
        while time.time() - t0 < warmup_s:
            ret, frame = cap.read()
            if ret and frame is not None and float(np.asarray(frame).mean()) >= 5.0:
                ok = True
                break
            time.sleep(0.05)
        cap.release()
        if ok:
            return idx
    return None


# ============================================================================
# CONFIGURATION
# ============================================================================

# Points MediaPipe → régions du visage (sélection pour animation ciblée)
# Index MediaPipe : https://mediapipe.dev/images/mobile/face-landmarks.svg

REGIONS = {
    "contour": list(range(0, 16)) + list(range(104, 152)),  # Jawline
    "sourcil_gauche": list(range(70, 76)),                   # Left eyebrow
    "sourcil_droit": list(range(105, 111)),                   # Right eyebrow
    "oeil_gauche": list(range(468, 473)) + [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246],  # Left eye
    "oeil_droit": list(range(473, 478)) + [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398],  # Right eye
    "nez": list(range(168, 204)),                             # Nose bridge & tip
    "bouche_ext": list(range(61, 84)),                        # Outer lips
    "bouche_int": list(range(78, 96)),                        # Inner lips
    "lèvre_basse": list(range(52, 61)),                       # Lower lip
    "lèvre_haute": list(range(48, 52)),                       # Upper lip
}

# Mapping des indices MediaPipe vers les vertices d'un mesh Blender standard (Head)
# Un mesh "Head" Blender a typiquement ~1000 vertices. On utilise les 468 premiers
# ou on fait un lissage/projection.

# ============================================================================
# EXTRACTEUR DE LANDMARKS (basé sur face_landmarks.py)
# ============================================================================

class FaceLandmarkExtractor:
    """Extrait les landmarks 3D depuis une webcam ou un fichier vidéo."""
    
    def __init__(self, model_path="face_landmarker.task"):
        self.model_path = model_path
        self.detector = None
        self._init_detector()
    
    def _init_detector(self):
        """Initialise le détecteur MediaPipe."""
        try:
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision
            from mediapipe.tasks.python.vision.core import image as mp_image_module
            
            base_options = python.BaseOptions(model_asset_path=self.model_path)
            options = vision.FaceLandmarkerOptions(
                base_options=base_options,
                output_face_blendshapes=False,
                output_facial_transformation_matrixes=True,
                num_faces=1
            )
            self.detector = vision.FaceLandmarker.create_from_options(options)
            self.mp_image_module = mp_image_module
        except ImportError as e:
            print(f"❌ Erreur d'import MediaPipe : {e}")
            sys.exit(1)
    
    def extract_frame(self, frame):
        """Extrait les landmarks 3D d'une frame (numpy array BGR)."""
        if self.detector is None:
            return None
        
        # Conversion en format MediaPipe
        mp_image = self.mp_image_module.Image(
            image_format=self.mp_image_module.ImageFormat.SRGB,
            data=np.asarray(frame)
        )
        
        results = self.detector.detect(mp_image)
        
        if not results.face_landmarks or len(results.face_landmarks) == 0:
            return None
        
        # Récupération du premier visage détecté
        landmarks = results.face_landmarks[0]
        image_width = frame.shape[1]
        image_height = frame.shape[0]
        
        points_3d = []
        for lm in landmarks:
            x = lm.x * image_width
            y = lm.y * image_height
            z = lm.z * image_width  # Échelle Z relative
            points_3d.append([x, y, z])
        
        return np.array(points_3d)  # Shape: (468, 3)
    
    def capture_webcam(self, duration_sec=10, output_json="face_animation_data.json",
                       device=None):
        """Capture depuis la webcam et sauvegarde les landmarks.

        device=None → auto-détection de la 1re caméra qui délivre du non-noir
        (évite l'index 0 « noir » quand plusieurs caméras existent)."""
        if device is None:
            device = _pick_working_camera()
            if device is None:
                raise WebcamUnavailable(
                    "Aucune caméra ne délivre d'image exploitable (toutes noires ou "
                    "muettes).\n" + _camera_context_hint()
                )
            print(f"📷 Caméra auto-sélectionnée : index {device}")

        cap = cv2.VideoCapture(device)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        if not cap.isOpened():
            cap.release()
            raise WebcamUnavailable(
                f"Webcam introuvable / inaccessible (index {device} fermé).\n"
                + _camera_context_hint()
            )

        print(f"📷 Capture en cours ({duration_sec}s)... Regarde la caméra. 'Q' pour arrêter.")

        frames_data = []
        frame_count = 0
        consecutive_fail = 0
        start_time = time.time()

        try:
            while True:
                elapsed = time.time() - start_time
                if elapsed > duration_sec:
                    break

                ret, frame = cap.read()
                # macOS/AVFoundation : les 1res frames peuvent revenir ret=False
                # (warmup) → NE PAS casser la boucle au 1er échec, tolérer un
                # paquet d'échecs consécutifs avant d'abandonner.
                if not ret or frame is None:
                    consecutive_fail += 1
                    if consecutive_fail > 90:  # ~3 s sans image → vraie panne
                        break
                    time.sleep(0.03)
                    continue
                consecutive_fail = 0

                frame = cv2.flip(frame, 1)  # Miroir

                landmarks = self.extract_frame(frame)

                if landmarks is not None:
                    frames_data.append(landmarks.tolist())
                    frame_count += 1

                # Affichage temps réel
                cv2.putText(frame, f"Points: {frame_count}", (10, 30),
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                cv2.imshow("Face Sequencer", frame)

                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    break

        finally:
            cap.release()
            cv2.destroyAllWindows()

        # Caméra ouverte mais 0 frame = perms TCC bloquées / aucun visage → échec
        # CLAIR. Sur macOS, cap.isOpened() peut valoir True SANS autorisation, mais
        # cap.read() renvoie alors ret=False à chaque tour → frames_data reste vide.
        if not frames_data:
            raise WebcamUnavailable(
                "0 image capturée — la caméra s'ouvre mais ne délivre aucune frame "
                "(permission refusée, ou aucun visage détecté).\n"
                + _camera_context_hint()
            )

        # Sauvegarde ESTAMPILLÉE « webcam » — jamais confondable avec du simulé.
        output = {
            "frame_count": len(frames_data),
            "resolution": [640, 480],
            "source": "webcam",
            "frames": frames_data
        }

        with open(output_json, 'w') as f:
            json.dump(output, f, indent=2)

        print(f"✅ {len(frames_data)} frames RÉELLES sauvegardées dans {output_json}")
        return output_json


# ============================================================================
# GÉNÉRATEUR DE SCRIPT BLANDER
# ============================================================================

def generate_blender_script(landmarks_data, output_blend="face_animation.blend"):
    """
    Génère un script Python Blender qui crée et anime le mesh de visage.
    
    Ce script est exécuté DANS Blender via bpy.
    """
    
    # Utilise .replace() au lieu de f-string pour éviter les problèmes avec {}
    script_template = '''#!/usr/bin/env python3
"""
Script généré automatiquement par sequencer_visage_3d.py
Crée un mesh de visage réaliste animé à partir de landmarks MediaPipe.
Utilise le Human Head (Anthropometric Head) de Blender.
"""

import bpy
import json
import math
import os

# ============================================================================
# CHARGEMENT DES DONNÉES
# ============================================================================

data_file = "{DATA_FILE}"
with open(data_file, 'r') as f:
    data = json.load(f)

frames_data = data["frames"]
frame_count = len(frames_data)
resolution = data["resolution"]

# ============================================================================
# CRÉATION DU MESH DE VISAGE RÉALISTE (Human Head)
# ============================================================================

def create_realistic_head():
    """Crée un Human Head réaliste via l'add-on Anthropometric de Blender."""
    
    # Active l'add-on Anthropometric Head si nécessaire
    addon_name = "object_anthropometric_head"
    if addon_name not in bpy.context.preferences.addons:
        print("⚠️  Add-on Anthropometric Head non trouvé, fallback sur UV Sphere...")
        return create_fallback_head()
    
    # Active l'add-on
    bpy.ops.preferences.addon_enable(module=addon_name)
    
    # Supprime les objets par défaut
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    
    # Crée le Human Head réaliste
    try:
        bpy.ops.object.anthropometric_head_add()
        mesh_obj = bpy.context.active_object
        mesh_obj.name = "RealisticHead"
        print(f"✅ Human Head créé avec {len(mesh_obj.data.vertices)} vertices")
        return mesh_obj
    except Exception as e:
        print(f"❌ Erreur création Human Head : {e}")
        return create_fallback_head()

def create_fallback_head():
    """Fallback : crée un mesh de visage basique à partir d'un UV Sphere."""
    
    # Supprime les objets par défaut
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    
    # Crée une UV Sphere comme base du visage
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=32, 
        ring_count=24,
        radius=1.0,
        location=(0, 0, 0)
    )
    
    mesh_obj = bpy.context.active_object
    mesh_obj.name = "FaceMesh"
    mesh = mesh_obj.data
    
    # Déformation vers une forme de visage
    deform_to_face_shape(mesh)
    
    return mesh_obj

def deform_to_face_shape(mesh):
    """Déforme la sphère pour ressembler à un visage humain."""
    
    verts = mesh.vertices
    
    for v in verts:
        x, y, z = v.co
        
        # Aplatissement vertical (visage plus large que haut)
        y *= 0.85
        
        # Élargissement des joues
        if abs(x) > 0.3:
            x *= 1.15
        
        # Aplatissement du front
        if y > 0.4:
            z *= 0.7
        
        # Protrusion du nez
        if abs(x) < 0.15 and y > -0.2 and y < 0.3:
            z += 0.15 * (1 - abs(x) / 0.15)
        
        # Protrusion des lèvres
        if y < -0.3 and abs(x) < 0.4:
            z += 0.1 * (1 - abs(x) / 0.4)
        
        v.co = (x, y, z)
    
    mesh.update()

def map_landmarks_to_vertices(mesh_obj, landmarks_3d):
    """
    Mappe les 468 landmarks MediaPipe vers les vertices du mesh.
    
    Méthode : projection 2D + recherche des vertices les plus proches.
    """
    
    # Normalisation des landmarks (de pixels à coordonnées Blender)
    w, h = resolution[0], resolution[1]
    
    # Facteur d'échelle : le mesh fait ~2 unités de large, l'image 640px
    scale = 2.0 / w
    
    # Inversion Y (MediaPipe: haut=0, Blender: haut=+Y)
    # Inversion Z (MediaPipe: z positif = proche, Blender: z positif = avant)
    
    vertices = mesh_obj.data.vertices
    num_verts = len(vertices)
    
    # Pour chaque vertex, trouver le landmark le plus proche en 2D
    vertex_to_landmark = {}
    
    for i, v in enumerate(vertices):
        vx, vy = v.co.x, v.co.y
        
        # Projection inverse : trouver le landmark correspondant
        best_dist = float('inf')
        best_lm_idx = 0
        
        for lm_idx, lm in enumerate(landmarks_3d):
            # Coordonnées 2D du landmark
            lm_x = lm[0] * scale
            lm_y = -lm[1] * scale  # Inversion Y
            
            # Distance 2D
            dist = math.sqrt((vx - lm_x)**2 + (vy - lm_y)**2)
            
            if dist < best_dist:
                best_dist = dist
                best_lm_idx = lm_idx
        
        vertex_to_landmark[i] = best_lm_idx
    
    return vertex_to_landmark

def animate_face(mesh_obj, frames_data, vertex_mapping):
    """Anime le mesh frame par frame."""
    
    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = len(frames_data)
    
    for frame_idx, landmarks in enumerate(frames_data):
        bpy.context.scene.frame_set(frame_idx + 1)
        
        # Normalisation des landmarks
        w, h = resolution[0], resolution[1]
        scale = 2.0 / w
        
        # Mise à jour des positions des vertices
        for vert_idx, lm_idx in vertex_mapping.items():
            lm = landmarks[lm_idx]
            
            # Coordonnées Blender
            x = lm[0] * scale
            y = -lm[1] * scale  # Inversion Y
            z = lm[2] * scale   # Z conservé
            
            # Application au vertex
            mesh_obj.data.vertices[vert_idx].co = (x, y, z)
        
        # Insertion des keyframes
        mesh_obj.keyframe_insert(data_path="data.vertices", index=-1, frame=frame_idx + 1)

# ============================================================================
# EXÉCUTION
# ============================================================================

print("🎭 Création du mesh de visage réaliste...")
mesh_obj = create_realistic_head()

print(f"📐 Mapping des {len(mesh_obj.data.vertices)} vertices...")
vertex_mapping = map_landmarks_to_vertices(mesh_obj, frames_data[0])

print(f"🎬 Animation de {frame_count} frames...")
animate_face(mesh_obj, frames_data, vertex_mapping)

# Sauvegarde
output_path = "{OUTPUT_BLEND}"
bpy.ops.wm.save_as_mainfile(filepath=output_path)
print(f"✅ Fichier sauvegardé : {output_path}")
'''
    
    # Remplace les placeholders
    base_name = output_blend.replace('.blend', '')
    data_file = f"{base_name}_data.json"
    
    script = script_template.replace("{DATA_FILE}", data_file)
    script = script.replace("{OUTPUT_BLEND}", output_blend)
    
    return script


# ============================================================================
# EXÉCUTION DIRECTE DANS BLANDER (si bpy disponible)
# ============================================================================

def run_in_blender(landmarks_data, output_blend="face_animation.blend"):
    """Exécute directement l'animation dans Blender (si bpy est importable)."""
    
    try:
        import bpy
    except ImportError:
        print("❌ bpy non disponible. Exécution hors de Blender.")
        print("💡 Génère un script Blender à exécuter manuellement.")
        return None
    
    print("🎭 Création du mesh de visage réaliste...")
    
    # Supprime les objets par défaut
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    
    # Tente de créer un Human Head réaliste
    mesh_obj = None
    try:
        # Active l'add-on Anthropometric Head
        addon_name = "object_anthropometric_head"
        if addon_name in bpy.context.preferences.addons:
            bpy.ops.object.anthropometric_head_add()
            mesh_obj = bpy.context.active_object
            mesh_obj.name = "RealisticHead"
            print(f"✅ Human Head créé avec {len(mesh_obj.data.vertices)} vertices")
        else:
            raise ImportError("Add-on non disponible")
    except Exception as e:
        print(f"⚠️  Human Head non disponible ({e}), fallback sur UV Sphere...")
        # Fallback : UV Sphere déformée
        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=32, ring_count=24, radius=1.0, location=(0, 0, 0)
        )
        mesh_obj = bpy.context.active_object
        mesh_obj.name = "FaceMesh"
        deform_to_face_shape_blender(mesh_obj.data)
    
    # Animation
    frames_data = landmarks_data["frames"]
    resolution = landmarks_data["resolution"]
    
    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = len(frames_data)
    
    print(f"🎬 Animation de {len(frames_data)} frames...")
    
    for frame_idx, landmarks in enumerate(frames_data):
        bpy.context.scene.frame_set(frame_idx + 1)
        
        w, h = resolution[0], resolution[1]
        scale = 2.0 / w
        
        # Pour chaque vertex, trouver le landmark le plus proche
        for vert_idx, v in enumerate(mesh_obj.data.vertices):
            vx, vy = v.co.x, v.co.y
            
            best_dist = float('inf')
            best_lm_idx = 0
            
            for lm_idx, lm in enumerate(landmarks):
                lm_x = lm[0] * scale
                lm_y = -lm[1] * scale
                
                dist = math.sqrt((vx - lm_x)**2 + (vy - lm_y)**2)
                
                if dist < best_dist:
                    best_dist = dist
                    best_lm_idx = lm_idx
            
            lm = landmarks[best_lm_idx]
            x = lm[0] * scale
            y = -lm[1] * scale
            z = lm[2] * scale
            
            mesh_obj.data.vertices[vert_idx].co = (x, y, z)
        
        mesh_obj.keyframe_insert(data_path="data.vertices", index=-1, frame=frame_idx + 1)
    
    # Sauvegarde
    bpy.ops.wm.save_as_mainfile(filepath=output_blend)
    print(f"✅ Fichier sauvegardé : {output_blend}")
    
    return output_blend


def deform_to_face_shape_blender(mesh):
    """Déforme une UV Sphere vers une forme de visage."""
    for v in mesh.vertices:
        x, y, z = v.co
        
        y *= 0.85
        if abs(x) > 0.3:
            x *= 1.15
        if y > 0.4:
            z *= 0.7
        if abs(x) < 0.15 and y > -0.2 and y < 0.3:
            z += 0.15 * (1 - abs(x) / 0.15)
        if y < -0.3 and abs(x) < 0.4:
            z += 0.1 * (1 - abs(x) / 0.4)
        
        v.co = (x, y, z)
    
    mesh.update()


# ============================================================================
# UTILITAIRES
# ============================================================================

def load_landmarks_from_json(filepath):
    """Charge les landmarks depuis un fichier JSON."""
    with open(filepath, 'r') as f:
        return json.load(f)


def generate_simulated_landmarks(output_json="face_animation_data.json", frames=60):
    """Génère des landmarks FACTICES pour tests — PAS un vrai visage. Le JSON est
    ESTAMPILLÉ `synthetic: true` → jamais confondable avec une vraie capture.
    Opt-in EXPLICITE uniquement (`--simulate`) : le fallback silencieux vers du
    simulé (cf. session 09/07 : faux visage produit cam éteinte) est INTERDIT."""
    import math

    base = []
    for i in range(468):
        a = (i / 468) * 2 * math.pi
        base.append([320 + 120 * math.cos(a), 240 + 150 * math.sin(a), 20 * math.sin(3 * a)])
    data = {
        "frame_count": frames,
        "resolution": [640, 480],
        "source": "simulated",
        "synthetic": True,
        "frames": [],
    }
    for f in range(frames):
        t = f / frames
        data["frames"].append(
            [[x + 4 * math.sin(6.2832 * t), y + 3 * math.cos(6.2832 * t), z]
             for x, y, z in base]
        )
    with open(output_json, 'w') as fp:
        json.dump(data, fp)
    print(f"⚠️  {frames} frames SIMULÉES (synthetic=true) écrites dans {output_json} "
          "— données de TEST, PAS un visage réel.")
    return output_json


def export_to_fbx(mesh_obj, output_fbx="face_animation.fbx"):
    """Exporte l'animation en FBX (pour Unity/Unreal)."""
    bpy.ops.export_scene.fbx(
        filepath=output_fbx,
        use_selection=True,
        apply_scale_options='FBX_SCALE_NONE',
        bake_anim=True,
        use_anim=True,
        use_anim_optimize=True,
        optimize_fps=True
    )
    print(f"✅ FBX exporté : {output_fbx}")


# ============================================================================
# INTERFACE LIGNE DE COMMANDE
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Séquençage de Visage Humain en 3D",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  # Capture 10 secondes et export Blender
  python sequencer_visage_3d.py --capture --duration 10
  
  # Replay depuis un fichier JSON
  python sequencer_visage_3d.py --playback --input face_animation_data.json
  
  # Génère juste le mesh statique
  python sequencer_visage_3d.py --mesh
        """
    )
    
    parser.add_argument('--capture', action='store_true', help='Capture depuis la webcam')
    parser.add_argument('--playback', action='store_true', help='Replay depuis un fichier JSON')
    parser.add_argument('--mesh', action='store_true', help='Génère un mesh statique')
    parser.add_argument('--duration', type=int, default=10, help='Durée de capture en secondes')
    parser.add_argument('--input', type=str, default=None, help='Fichier JSON d\'entrée')
    parser.add_argument('--output', type=str, default="face_animation.blend", help='Fichier de sortie')
    parser.add_argument('--device', type=int, default=None,
                        help='Index caméra (défaut: auto — 1re caméra non-noire)')
    parser.add_argument('--simulate', action='store_true',
                        help='Génère des landmarks FACTICES estampillés (test only, PAS un vrai visage)')

    args = parser.parse_args()

    # Mode simulé EXPLICITE — court-circuite webcam + mediapipe. Données estampillées.
    if args.simulate:
        print("⚠️  MODE SIMULÉ explicite demandé — données de TEST factices.")
        generate_simulated_landmarks("face_animation_data.json")
        return

    # Initialisation
    model_path = "face_landmarker.task"
    if not os.path.exists(model_path):
        print("📥 Téléchargement du modèle MediaPipe...")
        import urllib.request
        url = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
        urllib.request.urlretrieve(url, model_path)
        print("✅ Modèle téléchargé.")
    
    extractor = FaceLandmarkExtractor(model_path)
    
    if args.capture:
        # Mode capture — échec DUR si webcam KO (jamais de fallback simulé muet).
        try:
            json_file = extractor.capture_webcam(
                args.duration, "face_animation_data.json", device=args.device)
        except WebcamUnavailable as e:
            print(f"\n❌ CAPTURE WEBCAM IMPOSSIBLE\n{e}\n")
            print("⛔ AUCUNE donnée simulée écrite (choix volontaire). Pour des "
                  "données de TEST factices, relance explicitement avec --simulate.")
            sys.exit(2)

        if json_file:
            landmarks_data = load_landmarks_from_json(json_file)
            
            # Vérifie si on est dans Blender
            try:
                import bpy
                if hasattr(bpy, 'data'):
                    print("🖥️  Exécution dans Blender...")
                    run_in_blender(landmarks_data, args.output)
                else:
                    raise ImportError("Pas dans Blender")
            except ImportError:
                print("📝 Génération du script Blender...")
                blender_script = generate_blender_script(landmarks_data, args.output)
                
                script_path = "generate_face_animation.py"
                with open(script_path, 'w') as f:
                    f.write(blender_script)
                
                print(f"✅ Script généré : {script_path}")
                print(f"💡 Pour l'exécuter : blender --background --python {script_path}")
    
    elif args.playback:
        input_file = args.input or "face_animation_data.json"
        
        if not os.path.exists(input_file):
            print(f"❌ Fichier non trouvé : {input_file}")
            return
        
        landmarks_data = load_landmarks_from_json(input_file)
        
        try:
            import bpy
            if hasattr(bpy, 'data'):
                print("🖥️  Exécution dans Blender...")
                run_in_blender(landmarks_data, args.output)
            else:
                raise ImportError("Pas dans Blender")
        except ImportError:
            print("📝 Génération du script Blender...")
            blender_script = generate_blender_script(landmarks_data, args.output)
            
            script_path = "generate_face_animation.py"
            with open(script_path, 'w') as f:
                f.write(blender_script)
            
            print(f"✅ Script généré : {script_path}")
            print(f"💡 Pour l'exécuter : blender --background --python {script_path}")
    
    elif args.mesh:
        # Mode mesh statique (juste pour visualiser la forme)
        try:
            import bpy
            if hasattr(bpy, 'data'):
                print("🖥️  Création du mesh réaliste dans Blender...")
                
                # Supprime les objets par défaut
                bpy.ops.object.select_all(action='SELECT')
                bpy.ops.object.delete(use_global=False)
                
                # Tente de créer un Human Head
                try:
                    addon_name = "object_anthropometric_head"
                    if addon_name in bpy.context.preferences.addons:
                        bpy.ops.object.anthropometric_head_add()
                        mesh_obj = bpy.context.active_object
                        mesh_obj.name = "RealisticHead"
                        print(f"✅ Human Head créé avec {len(mesh_obj.data.vertices)} vertices")
                    else:
                        raise ImportError("Add-on non disponible")
                except Exception as e:
                    print(f"⚠️  Human Head non disponible ({e}), fallback sur UV Sphere...")
                    bpy.ops.mesh.primitive_uv_sphere_add(
                        segments=32, ring_count=24, radius=1.0, location=(0, 0, 0)
                    )
                    mesh_obj = bpy.context.active_object
                    mesh_obj.name = "FaceMesh"
                    deform_to_face_shape_blender(mesh_obj.data)
                
                print("✅ Mesh créé. Appuie sur Tab pour passer en mode Édition.")
            else:
                raise ImportError("Pas dans Blender")
        except ImportError:
            print("❌ bpy non disponible. Exécution hors de Blender.")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
