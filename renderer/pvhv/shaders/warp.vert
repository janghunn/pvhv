#version 430 core
// Forward warping of one layer (plan section G): Wg*Hg vertices x 4 instances,
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
