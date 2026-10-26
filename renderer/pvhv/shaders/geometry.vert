#version 430 core
// Geometry pass vertex shader (plan section B); shared by layer 0, peeling and the
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
