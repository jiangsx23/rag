const fs = require('fs');
const path = require('path');

// Read the HTML file
let html = fs.readFileSync('docs/technical_whitepaper.html', 'utf-8');

// Extract all mermaid diagram blocks
const blockRegex = /<div class="mermaid">\s*([\s\S]*?)<\/div>\s*<!--\s*mermaid\s*-->/g;
// Also match without the comment
const blockRegex2 = /<div class="mermaid">\s*([\s\S]*?)<\/div>/g;

let matches = [];
let match;
while ((match = blockRegex2.exec(html)) !== null) {
    // Avoid matching the same block twice
    if (match[1].trim().length > 0 && !match[1].includes('<')) {
        matches.push(match[1].trim());
    }
}

// Deduplicate
matches = [...new Set(matches)];
console.log(`Found ${matches.length} unique mermaid diagrams`);

const mermaidDir = path.join(__dirname, 'docs', 'mermaid_svgs');
if (!fs.existsSync(mermaidDir)) {
    fs.mkdirSync(mermaidDir, { recursive: true });
}

// Use the mermaid API to render each diagram
async function render() {
    const mermaid = await import('mermaid');
    mermaid.default.initialize({
        startOnLoad: false,
        theme: 'default',
        themeVariables: {
            primaryColor: '#2563eb',
            primaryTextColor: '#fff',
            primaryBorderColor: '#1d4ed8',
            lineColor: '#94a3b8',
            secondaryColor: '#eff6ff',
            tertiaryColor: '#f9fafb',
            fontSize: '14px',
        },
        flowchart: { useMaxWidth: true, htmlLabels: true },
        sequence: { useMaxWidth: true },
    });

    for (let i = 0; i < matches.length; i++) {
        const diagram = matches[i];
        const diagramId = `diagram-${i}`;
        
        try {
            const { svg } = await mermaid.default.render(diagramId, diagram);
            const svgPath = path.join(mermaidDir, `${diagramId}.svg`);
            fs.writeFileSync(svgPath, svg, 'utf-8');
            console.log(`✅ Diagram ${i + 1}/${matches.length}: ${svgPath}`);
            
            // Replace in HTML: find this specific diagram content and replace the div with SVG
            const escapedDiagram = diagram.replace(/[.*+?^${}()|[\]\]/g, '\$&');
            const divRegex = new RegExp(
                `<div class="mw">\s*<div class="mermaid">\s*${escapedDiagram}\s*<\/div>\s*<\/div>`
            );
            
            const svgContent = fs.readFileSync(svgPath, 'utf-8');
            const replacement = `<div class="mw" style="text-align:center;padding:10px;">\n${svgContent}\n</div>`;
            
            // Try replacing with mw wrapper first, then without
            let newHtml = html.replace(divRegex, replacement);
            if (newHtml === html) {
                // Try without mw wrapper
                const divRegex2 = new RegExp(
                    `<div class="mermaid">\s*${escapedDiagram}\s*<\/div>`
                );
                newHtml = html.replace(divRegex2, replacement);
            }
            
            if (newHtml !== html) {
                html = newHtml;
                console.log(`   ↳ Replaced in HTML`);
            } else {
                console.log(`   ⚠️ Could not find in HTML (may need manual check)`);
            }
        } catch (err) {
            console.error(`❌ Diagram ${i + 1} failed: ${err.message}`);
        }
    }
    
    // Remove the CDN script and mermaid initialization
    html = html.replace(
        /<script src="https:\/\/cdn\.jsdelivr\.net\/npm\/mermaid@[^"]+"><\/script>/,
        ''
    );
    html = html.replace(
        /<script>\s*mermaid\.initialize\([\s\S]*?<\/script>/,
        ''
    );
    
    // Write updated HTML
    fs.writeFileSync('docs/technical_whitepaper.html', html, 'utf-8');
    console.log('\n✅ Updated HTML written to docs/technical_whitepaper.html');
}

render().catch(err => {
    console.error('Fatal:', err);
    process.exit(1);
});
