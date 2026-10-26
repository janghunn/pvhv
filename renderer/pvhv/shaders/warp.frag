#version 430 core
// Forward warping fragment shader (plan section G).

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
