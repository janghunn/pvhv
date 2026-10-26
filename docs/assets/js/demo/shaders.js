// GLSL ES 3.00 sources of the demo.  The four pipeline shaders are the bodies of
// renderer/pvhv/shaders/{geometry.vert,peel.frag,warp.vert,warp.frag} unchanged; only the
// '#version 430 core' line is replaced by the ES header below (checked by
// demo/tests/static.test.mjs).  The composite shaders are the demo's own display pass.

const HEADER = `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;
precision highp usampler2D;
`;

export const GEOMETRY_VERT = HEADER + `// Geometry pass vertex shader (plan section B); shared by layer 0, peeling and the
// reference render.  Constructs are limited to what GLSL ES 3.00 also offers.

layout(location = 0) in vec3 in_pos;
layout(location = 1) in vec3 in_normal;
layout(location = 2) in vec2 in_uv;
layout(location = 3) in uint in_item;

uniform mat4 model;
uniform mat4 view;
uniform mat4 proj;
uniform mat3 normalMatrix;   // inverse transpose of the model's upper 3x3

out vec3 vNormal;            // world space
out float vDepth;            // linear depth z = -Z_cam (perspective-correct)
out vec2 vUV;
flat out uint vItem;

void main() {
    vec4 world = model * vec4(in_pos, 1.0);
    vec4 cam = view * world;
    vNormal = normalMatrix * in_normal;
    vDepth = -cam.z;
    vUV = in_uv;
    vItem = in_item;
    gl_Position = proj * cam;
}
`;

export const PEEL_FRAG = HEADER + `// Geometry/peeling fragment shader (plan sections B, C, D, E).
// mode 0: plain z-buffer render (layer 0 and the reference novel view)
// mode 1: BDP, 2: UDP, 3: EDP (BDP rule AND inPVHV), 4: classify (BDP rule; the
// inPVHV result is written to outFlag instead of discarding: 1 = culled, 2 = kept).
// Constructs are limited to what GLSL ES 3.00 also offers (no SSBO, no images).

in vec3 vNormal;
in float vDepth;
in vec2 vUV;
flat in uint vItem;

layout(location = 0) out vec4 outColor;   // RGBA8, sRGB-encoded
layout(location = 1) out float outDepth;  // R32F, linear depth z
layout(location = 2) out uint outItem;    // R32UI, item ID (0 = empty)
layout(location = 3) out uint outFlag;    // R32UI, classify mode only

// material
uniform vec3 baseColor;
uniform bool hasTexture;
uniform sampler2D baseTex;      // sRGB texture (decoded to linear by the sampler)
uniform bool hasAlphaTex;
uniform sampler2D alphaTex;     // map_d, first channel
uniform bool alphaTest;         // cutoff 0.5

// peeling
uniform int mode;
uniform sampler2D prevDepth;    // layer h-1 linear depth
uniform usampler2D prevItem;    // layer h-1 item
uniform ivec2 srcSize;          // (Wg, Hg)
uniform float eps;              // BDP threshold, normalized linear depth
uniform float delta;            // connectivity threshold, normalized linear depth
uniform float E;                // novel-view bound radius (world units)
uniform float F;                // focal length (pixels)
uniform float near;
uniform float far;
uniform float margin;           // pixels (deviation D1)
uniform int nSamples;
uniform vec2 samples[64];       // EDP probe points (section E, probe_samples), uploaded from Python
uniform int searchMode;         // 0 = SAMPLED, 1 = EXHAUSTIVE
uniform float maxExhaustiveRadius;

const vec3 LIGHT = vec3(0.4, 0.8, 0.45);

float normDepth(float z) { return (z - near) / (far - near); }

vec3 linearToSrgb(vec3 c) {
    c = clamp(c, 0.0, 1.0);
    return mix(c * 12.92, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, step(vec3(0.0031308), c));
}

float pixelHash01(ivec2 pix) {
    uint h = uint(pix.x) * 1973u + uint(pix.y) * 9277u + 26699u;
    h ^= h >> 16;
    h *= 0x7feb352du;
    h ^= h >> 15;
    h *= 0x846ca68bu;
    h ^= h >> 16;
    return float(h >> 8) / 16777216.0;
}

// Algorithm 1 probe: true = an edge (or unknown occluder) was found at w.
bool probe(ivec2 w, float qn, uint qItem) {
    if (w.x < 0 || w.y < 0 || w.x >= srcSize.x || w.y >= srcSize.y) return true;  // D5
    uint wi = texelFetch(prevItem, w, 0).r;
    if (wi != qItem) return true;                                // line 4 (empty -> edge)
    float wn = normDepth(texelFetch(prevDepth, w, 0).r);
    if (wn > qn + delta) return true;                            // line 5: farther outer surface
    if (wn < qn - delta) return true;                            // line 17: conservative approximation
    return false;                                                // line 14: connected surface
}

bool inPVHV(ivec2 pix, float pz, float qz, float qn, uint qItem) {
    float R = F * E * (1.0 / qz - 1.0 / pz);                     // flat assumption f_z := q_z
    float Reff = R + margin;                                     // D1
    if (searchMode == 0) {
        float th = 6.28318530718 * pixelHash01(pix);             // D2
        float c = cos(th);
        float s = sin(th);
        for (int k = 0; k < nSamples; ++k) {
            vec2 d = samples[k];
            vec2 o = Reff * vec2(c * d.x - s * d.y, s * d.x + c * d.y);
            ivec2 w = ivec2(floor(vec2(pix) + 0.5 + o));
            if (probe(w, qn, qItem)) return true;
        }
        return false;
    }
    if (Reff > maxExhaustiveRadius) return true;                 // conservative
    int r = int(floor(Reff));
    float r2 = Reff * Reff;
    for (int dy = -r; dy <= r; ++dy) {
        for (int dx = -r; dx <= r; ++dx) {
            if (dx == 0 && dy == 0) continue;
            if (float(dx * dx + dy * dy) > r2) continue;
            if (probe(pix + ivec2(dx, dy), qn, qItem)) return true;
        }
    }
    return false;
}

void main() {
    // material lookups first (implicit derivatives need uniform control flow)
    vec4 texel = hasTexture ? texture(baseTex, vUV) : vec4(1.0);
    float alpha = hasAlphaTex ? texture(alphaTex, vUV).r : texel.a;
    if (alphaTest && alpha < 0.5) discard;

    float pz = vDepth;
    uint flag = 0u;
    if (mode != 0) {
        ivec2 pix = ivec2(gl_FragCoord.xy);
        uint qItem = texelFetch(prevItem, pix, 0).r;
        if (qItem == 0u) discard;                                // empty in h-1 -> nothing in h
        float qz = texelFetch(prevDepth, pix, 0).r;
        float pn = normDepth(pz);
        float qn = normDepth(qz);
        if (mode == 2) {
            float a = qz / (2.0 * F);                            // inscribed disk of the pixel footprint
            if (E <= a) discard;                                 // unbounded umbra
            float umbraN = normDepth(qz * E / (E - a));
            if (pn <= max(qn + eps, umbraN)) discard;
        } else {
            if (pn <= qn + eps) discard;
            if (mode == 3 && !inPVHV(pix, pz, qz, qn, qItem)) discard;
            if (mode == 4) flag = inPVHV(pix, pz, qz, qn, qItem) ? 2u : 1u;
        }
    }

    vec3 n = normalize(vNormal);
    if (!gl_FrontFacing) n = -n;
    vec3 base = baseColor * texel.rgb;
    vec3 c = base * (0.25 + 0.75 * max(0.0, dot(n, normalize(LIGHT))));
    outColor = vec4(linearToSrgb(c), 1.0);
    outDepth = pz;
    outItem = vItem;
    outFlag = flag;
}
`;

export const WARP_VERT = HEADER + `// Forward warping of one layer (plan section G): Wg*Hg vertices x 4 instances,
// one point per (texel, subpixel offset).  GLSL ES 3.00-compatible constructs.

uniform sampler2D layerColor;
uniform sampler2D layerDepth;
uniform usampler2D layerItem;
uniform ivec2 srcSize;     // (Wg, Hg)
uniform vec2 dstSize;      // (W, H)
uniform vec2 t;            // lens offset (tx, ty) in source camera space
uniform float F;
uniform float near;
uniform float far;

flat out vec4 vColor;
flat out float vDepth;
flat out uint vItem;

const vec2 OFFS[4] = vec2[4](vec2(-0.25, -0.25), vec2(0.25, -0.25), vec2(-0.25, 0.25), vec2(0.25, 0.25));

void main() {
    int k = gl_VertexID;
    ivec2 ij = ivec2(k % srcSize.x, k / srcSize.x);
    uint it = texelFetch(layerItem, ij, 0).r;
    gl_PointSize = 1.0;
    vItem = it;
    if (it == 0u) {
        vColor = vec4(0.0);
        vDepth = 0.0;
        gl_Position = vec4(2.0, 2.0, 2.0, 1.0);                  // clipped
        return;
    }
    float z = texelFetch(layerDepth, ij, 0).r;
    vColor = texelFetch(layerColor, ij, 0);
    vDepth = z;
    vec2 uv = vec2(ij) + 0.5 + OFFS[gl_InstanceID];
    float inv = 1.0 / z;
    vec2 p = 0.5 * dstSize + (uv - 0.5 * vec2(srcSize)) - F * t * inv;
    float zn = (z - near) / (far - near);
    gl_Position = vec4(2.0 * p / dstSize - 1.0, 2.0 * zn - 1.0, 1.0);
}
`;

export const WARP_FRAG = HEADER + `// Forward warping fragment shader (plan section G).

flat in vec4 vColor;
flat in float vDepth;
flat in uint vItem;

uniform uint layerIndex;

layout(location = 0) out vec4 outColor;   // RGBA8
layout(location = 1) out float outDepth;  // R32F linear depth
layout(location = 2) out uint outItem;    // R32UI item
layout(location = 3) out uint outLayer;   // R32UI layer + 1 (0 = hole)

void main() {
    outColor = vColor;
    outDepth = vDepth;
    outItem = vItem;
    outLayer = layerIndex + 1u;
}
`;

// Display pass (demo only): warped colour where a layer won; otherwise magenta where the
// direct reference render has geometry (disocclusion hole), else the scene background.
export const COMPOSITE_VERT = HEADER + `
void main() {
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    gl_Position = vec4(2.0 * p - 1.0, 0.0, 1.0);
}
`;

export const COMPOSITE_FRAG = HEADER + `
uniform sampler2D warpColor;
uniform usampler2D warpLayer;
uniform usampler2D refItem;
uniform vec3 background;   // sRGB-encoded, 0..1
uniform bool highlight;

out vec4 outColor;

const vec3 HOLE = vec3(1.0, 0.0, 1.0);
const vec3 HIDDEN = vec3(230.0, 159.0, 0.0) / 255.0;

void main() {
    ivec2 pix = ivec2(gl_FragCoord.xy);
    uint layer = texelFetch(warpLayer, pix, 0).r;
    vec3 c;
    if (layer > 0u) {
        c = texelFetch(warpColor, pix, 0).rgb;
        if (highlight && layer >= 2u) c = mix(c, HIDDEN, 0.5);
    } else if (texelFetch(refItem, pix, 0).r != 0u) {
        c = HOLE;
    } else {
        c = background;
    }
    outColor = vec4(c, 1.0);
}
`;
