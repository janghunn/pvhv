"""Stage 2 of the teaser: path-trace the San Miguel source view in Blender Cycles.

Run headless with Blender 5.2.2:

    blender -b --factory-startup --python-exit-code 1 -P scene_teaser.py -- \
        --models-cache <cache root> --out build/beauty.png [--device-index N]

The camera is read from renderer/pvhv/scenes/sanmiguel.json (no duplicated constants) and
converted to Blender's Z-up world so that the image is pixel-aligned with the central
W x H crop of the re-implementation's source view.  OBJ (x, y, z) imports as Blender
(x, -z, y), i.e. C = [[1,0,0],[0,0,-1],[0,1,0]], with forward -Z / up Y.

``--depth-out FILE.npy`` is a one-time alignment check instead of the beauty render: it
renders camera-axis depth (1 centred sample per pixel, alpha-tested foliage) and saves it
as a top-row-first float32 array (0 = no geometry).
"""
import argparse
import json
import math
import os
import re
import sys
import time

import bpy
import numpy as np
from mathutils import Matrix, Vector

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCENE_JSON = os.path.join(REPO, "renderer", "pvhv", "scenes", "sanmiguel.json")

# ---- look (tuned by eye; see README "Render settings") -----------------------------------
SAMPLES = 384
SEED = 0
SUN_ELEVATION = 50.0        # degrees above the horizon (lower is blocked by the 3-storey arcades)
SUN_AZIMUTH = 40.0          # degrees, Blender world: 0 = sun towards +Y, 90 = towards +X
SUN_STRENGTH = 10.0         # W/m^2
SUN_ANGLE = 1.2             # apparent diameter, degrees (soft late-afternoon shadows)
SUN_COLOR = (1.0, 0.66, 0.38)
SKY_STRENGTH = 0.6
SKY_WARM_MIX = 0.6          # 0 = physical sky colour, 1 = SKY_WARM_COLOR
SKY_WARM_COLOR = (0.32, 0.27, 0.22)
EXPOSURE = 1.7
LOOK = "AgX - Base Contrast"
MIN_ROUGHNESS = 0.55
MAX_SPECULAR = 0.35         # Principled "Specular IOR Level"


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="scene_teaser.py")
    p.add_argument("--models-cache", default=os.environ.get("PVHV_MODELS_CACHE",
                                                           os.path.join(REPO, "models", "cache")))
    p.add_argument("--out", default=os.path.join(HERE, "build", "beauty.png"))
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--samples", type=int, default=SAMPLES)
    p.add_argument("--device-index", type=int, default=int(os.environ.get("CYCLES_DEVICE_INDEX", "0")),
                   help="index among the visible OptiX (else CUDA) GPUs, in PCI bus order")
    p.add_argument("--depth-out", default=None, help="alignment check: write camera depth .npy instead")
    return p.parse_args(argv)


# ---- camera ----------------------------------------------------------------------------
C = Matrix(((1, 0, 0), (0, 0, -1), (0, 1, 0)))


def _normalize(v):
    v = Vector(v)
    return v / v.length


def setup_camera(scene, cfg, width, height):
    c = cfg["camera"]
    eye, target, up = Vector(c["eye"]), Vector(c["target"]), Vector(c["up"])
    back = _normalize(eye - target)           # same basis as renderer/pvhv/camera.py
    right = _normalize(up.cross(back))
    up2 = back.cross(right)
    m = Matrix.Identity(4)
    for col, v in enumerate((C @ right, C @ up2, C @ back, C @ eye)):
        for row in range(3):
            m[row][col] = v[row]
    cam_data = bpy.data.cameras.new("source")
    cam_data.type = "PERSP"
    cam_data.sensor_fit = "VERTICAL"
    cam_data.sensor_height = 24.0
    cam_data.lens = 24.0 / (2.0 * math.tan(math.radians(float(c["fovy"])) / 2.0))
    cam_data.shift_x = cam_data.shift_y = 0.0
    cam_data.clip_start, cam_data.clip_end = float(cfg["near"]), float(cfg["far"])
    cam_data.dof.use_dof = False
    cam = bpy.data.objects.new("source", cam_data)
    scene.collection.objects.link(cam)
    cam.matrix_world = m
    scene.camera = cam
    r = scene.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = width, height, 100
    r.pixel_aspect_x = r.pixel_aspect_y = 1.0
    return cam


# ---- materials -------------------------------------------------------------------------
def fix_materials():
    """Alpha-test map_Kd alpha like the renderer, drop mis-mapped bump/normal maps, tame gloss."""
    missing = [f"{mat.name}: {n.image.filepath if n.image else None}"
               for mat in bpy.data.materials if mat.node_tree
               for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeTexImage"
               if n.image is None or n.image.size[0] == 0]   # .size loads the file; 0 = failed to load
    if missing:
        raise RuntimeError(f"{len(missing)} texture(s) failed to load: " + "; ".join(missing[:10]))
    n_alpha = n_bump = 0
    n_gloss = {}
    for mat in bpy.data.materials:
        if not mat.node_tree:
            continue
        nt = mat.node_tree
        bsdf = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
        if bsdf is None:
            continue
        for n in [n for n in nt.nodes if n.bl_idname in ("ShaderNodeNormalMap", "ShaderNodeBump")]:
            for link in list(n.outputs[0].links):
                nt.links.remove(link)
            for link in list(n.inputs["Color" if "Color" in n.inputs else "Height"].links):
                img_node = link.from_node
                nt.nodes.remove(img_node)
            nt.nodes.remove(n)
            n_bump += 1
        inp = bsdf.inputs
        for name in ("Specular IOR Level", "Roughness", "Metallic"):   # textured gloss (map_Ks, map_Ns, ...)
            for link in list(inp[name].links):
                src = link.from_node
                nt.links.remove(link)
                n_gloss[name] = n_gloss.get(name, 0) + 1
                if src.bl_idname == "ShaderNodeTexImage" and not any(o.links for o in src.outputs):
                    nt.nodes.remove(src)
        inp["Roughness"].default_value = max(inp["Roughness"].default_value, MIN_ROUGHNESS)
        inp["Specular IOR Level"].default_value = min(inp["Specular IOR Level"].default_value, MAX_SPECULAR)
        inp["Metallic"].default_value = 0.0
        inp["Alpha"].default_value = 1.0     # the renderer treats d >= 0.5 as opaque
        for link in list(inp["Alpha"].links):
            nt.links.remove(link)
        base = inp["Base Color"].links
        if base and base[0].from_node.bl_idname == "ShaderNodeTexImage":
            tex = base[0].from_node
            if tex.image.channels == 4:
                gt = nt.nodes.new("ShaderNodeMath")
                gt.operation = "GREATER_THAN"
                gt.inputs[1].default_value = 0.5
                nt.links.new(tex.outputs["Alpha"], gt.inputs[0])
                nt.links.new(gt.outputs[0], inp["Alpha"])
                n_alpha += 1
    gloss = ", ".join(f"{k} {v}" for k, v in sorted(n_gloss.items())) or "none"
    print(f"materials: alpha-tested {n_alpha}, removed {n_bump} bump/normal maps, unlinked gloss maps ({gloss}), "
          f"roughness >= {MIN_ROUGHNESS}, specular <= {MAX_SPECULAR}")


def depth_materials():
    """Alignment check: every material emits camera-axis depth, alpha-tested where textured."""
    for mat in bpy.data.materials:
        if not mat.node_tree:
            continue
        nt = mat.node_tree
        bsdf = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)
        out = next((n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputMaterial"), None)
        if bsdf is None or out is None:
            continue
        cd = nt.nodes.new("ShaderNodeCameraData")
        em = nt.nodes.new("ShaderNodeEmission")
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        mix = nt.nodes.new("ShaderNodeMixShader")
        nt.links.new(cd.outputs["View Z Depth"], em.inputs["Strength"])
        em.inputs["Color"].default_value = (1, 1, 1, 1)
        alpha = bsdf.inputs["Alpha"]
        if alpha.links:
            nt.links.new(alpha.links[0].from_socket, mix.inputs[0])
        else:
            mix.inputs[0].default_value = 1.0
        nt.links.new(tr.outputs[0], mix.inputs[1])
        nt.links.new(em.outputs[0], mix.inputs[2])
        nt.links.new(mix.outputs[0], out.inputs["Surface"])


# ---- lighting --------------------------------------------------------------------------
def setup_lighting(scene):
    el, az = math.radians(SUN_ELEVATION), math.radians(SUN_AZIMUTH)
    to_sun = Vector((math.cos(el) * math.sin(az), math.cos(el) * math.cos(az), math.sin(el)))
    world = bpy.data.worlds.new("sky")
    scene.world = world
    nt = world.node_tree if world.node_tree else None
    if nt is None:
        world.use_nodes = True
        nt = world.node_tree
    nt.nodes.clear()
    sky = nt.nodes.new("ShaderNodeTexSky")
    sky.sky_type = "MULTIPLE_SCATTERING"
    sky.sun_disc = False
    sky.sun_elevation = el
    sky.sun_rotation = az
    mix = nt.nodes.new("ShaderNodeMix")          # desaturate the sky fill towards a warm grey
    mix.data_type = "RGBA"
    mix.inputs["Factor"].default_value = SKY_WARM_MIX
    mix.inputs["B"].default_value = (*SKY_WARM_COLOR, 1.0)
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = SKY_STRENGTH
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(sky.outputs["Color"], mix.inputs["A"])
    nt.links.new(mix.outputs["Result"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    sun_data = bpy.data.lights.new("sun", "SUN")
    sun_data.energy = SUN_STRENGTH
    sun_data.angle = math.radians(SUN_ANGLE)
    sun_data.color = SUN_COLOR
    sun = bpy.data.objects.new("sun", sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = (-to_sun).to_track_quat("-Z", "Y").to_euler()


def setup_black_world(scene):
    world = bpy.data.worlds.new("black")
    scene.world = world
    world.color = (0.0, 0.0, 0.0)
    if world.node_tree:
        world.node_tree.nodes.clear()


# ---- cycles ----------------------------------------------------------------------------
_PCI = re.compile(r"([0-9a-fA-F]{4}:)?[0-9a-fA-F]{2}:[0-9a-fA-F]{2}(\.[0-9a-fA-F])?")


def pci_order(ids):
    """Indices of Cycles device ids sorted by the PCI address in each id (last match, lower case,
    domain 0000 and function .0 filled in).  Without an address in every id: the given order."""
    keys = []
    for dev_id in ids:
        found = list(_PCI.finditer(dev_id))
        if not found:
            print(f"warning: no PCI address in Cycles device id {dev_id!r}; using the device-list order")
            return list(range(len(ids)))
        addr = found[-1].group(0).lower()
        keys.append(("" if found[-1].group(1) else "0000:") + addr + ("" if found[-1].group(2) else ".0"))
    return sorted(range(len(ids)), key=lambda i: keys[i])


def setup_cycles(scene, samples, device_index):
    scene.render.engine = "CYCLES"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    chosen = None
    for dev_type in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = dev_type
        except TypeError:
            continue
        prefs.refresh_devices()
        gpus = [d for d in prefs.devices if d.type == dev_type]
        gpus = [gpus[i] for i in pci_order([d.id for d in gpus])]
        if gpus:
            if not 0 <= device_index < len(gpus):
                raise RuntimeError(f"--device-index {device_index}: only {len(gpus)} {dev_type} GPU(s)")
            for d in prefs.devices:
                d.use = False
            chosen = gpus[device_index]
            chosen.use = True
            break
    if chosen is None:
        raise RuntimeError("no OptiX/CUDA GPU visible to Cycles")
    print(f"cycles device: {prefs.compute_device_type} {chosen.name} [{chosen.id}]")
    c = scene.cycles
    c.device = "GPU"
    c.samples = samples
    c.use_adaptive_sampling = False
    c.seed = SEED
    c.use_animated_seed = False
    c.max_bounces = 6
    c.diffuse_bounces = 4
    c.glossy_bounces = 2
    c.transmission_bounces = 4
    c.transparent_max_bounces = 24
    c.sample_clamp_direct = 0.0
    c.sample_clamp_indirect = 8.0
    c.caustics_reflective = c.caustics_refractive = False
    c.use_denoising = True
    c.denoiser = "OPENIMAGEDENOISE"
    c.denoising_use_gpu = False        # CPU OIDN: deterministic run to run
    c.denoising_input_passes = "RGB_ALBEDO_NORMAL"
    c.denoising_prefilter = "ACCURATE"
    scene.render.use_persistent_data = False
    scene.render.film_transparent = False


def render_beauty(scene, out):
    vs = scene.view_settings
    scene.display_settings.display_device = "sRGB"
    vs.view_transform = "AgX"
    vs.look = LOOK
    vs.exposure = EXPOSURE
    vs.gamma = 1.0
    im = scene.render.image_settings
    im.file_format = "PNG"
    im.color_mode = "RGB"
    im.color_depth = "16"
    im.compression = 15
    scene.render.filepath = out
    bpy.ops.render.render(write_still=True)


def render_depth(scene, out_npy):
    c = scene.cycles
    c.samples = 1
    c.use_denoising = False
    c.max_bounces = 0
    c.transparent_max_bounces = 64
    scene.cycles.filter_width = 0.01           # one ray through the pixel centre, like rasterisation
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    im = scene.render.image_settings
    im.file_format = "OPEN_EXR"
    im.color_mode = "RGB"
    im.color_depth = "32"
    exr = out_npy[:-4] + ".exr"
    scene.render.filepath = exr
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(exr)
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    depth = px.reshape(h, w, 4)[::-1, :, 0].copy()   # Blender pixels are bottom-row-first
    np.save(out_npy, depth)
    print(f"wrote {out_npy} ({w}x{h})")


def main():
    args = parse_args()
    t0 = time.perf_counter()
    with open(SCENE_JSON) as fh:
        cfg = json.load(fh)
    obj = os.path.join(args.models_cache, cfg["objects"][0]["obj"])
    if not os.path.isfile(obj):
        raise RuntimeError(f"{obj} not found - run models/fetch_models.py")
    if any(abs(v - (1.0 if i == j else 0.0)) > 0 for i, row in enumerate(cfg["objects"][0]["transform"])
           for j, v in enumerate(row)):
        raise RuntimeError("non-identity object transform is not supported")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    bpy.ops.wm.obj_import(filepath=obj, forward_axis="NEGATIVE_Z", up_axis="Y")
    t_import = time.perf_counter()
    setup_camera(scene, cfg, args.width, args.height)
    fix_materials()
    setup_cycles(scene, args.samples, args.device_index)
    os.makedirs(os.path.dirname(os.path.abspath(args.depth_out or args.out)), exist_ok=True)
    if args.depth_out:
        depth_materials()
        setup_black_world(scene)
        render_depth(scene, os.path.abspath(args.depth_out))
    else:
        setup_lighting(scene)
        render_beauty(scene, os.path.abspath(args.out))
    t1 = time.perf_counter()
    print(f"scene_teaser: import {t_import - t0:.1f}s, setup+render {t1 - t_import:.1f}s, "
          f"total {t1 - t0:.1f}s")


if __name__ == "__main__":
    main()
