// Build-time only: reuse Scout's existing component classes, tokens and licensed assets.
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../../..');
const dashboard = path.join(root, 'dashboard');
const req = createRequire(path.join(dashboard, 'package.json'));
const output = path.resolve(__dirname, '../public');
const postcss = req('postcss');
const tailwind = req('@tailwindcss/postcss');
const React = req('react');
const vm = require('node:vm');
const { renderToStaticMarkup } = req('react-dom/server');
const icons = req('lucide-react');
async function main() {
  fs.mkdirSync(output, { recursive: true });
  const policySource=fs.readFileSync(path.join(dashboard,'components/access-policies.tsx'),'utf8');
  const policyArray=policySource.slice(policySource.indexOf('= [')+2,policySource.indexOf('];')+1);
  fs.writeFileSync(path.join(output,'policies.json'),JSON.stringify(vm.runInNewContext('('+policyArray+')'),null,2)+'\n');
  const cssPath = path.join(dashboard, 'app/globals.css');
  const css = fs.readFileSync(cssPath, 'utf8') + '\n@source "../../rust/dashboard/src";\n';
  const result = await postcss([tailwind({ base: dashboard, optimize: true })]).process(css, { from: cssPath });
  fs.writeFileSync(path.join(output, 'scout.css'), result.css);
  fs.mkdirSync(path.join(output, 'fonts'), { recursive: true });
  for (const font of ['Inter.woff2', 'Mono.woff2']) fs.copyFileSync(path.join(dashboard, 'assets/fonts', font), path.join(output, 'fonts', font));
  for (const [from, to] of [['scout-icon.svg','scout-icon.svg'],['scout-icon-512.png','scout-icon.png'],['google-g.png','google-g.png']]) fs.copyFileSync(path.join(dashboard, 'assets', from), path.join(output, to));
  fs.mkdirSync(path.join(output, 'icons'), { recursive: true });
  for (const [name, component] of Object.entries({ search:icons.Search, moon:icons.Moon, sun:icons.Sun, layout:icons.LayoutGrid, chat:icons.MessageSquare, tasks:icons.ListTodo, transactions:icons.Receipt, refresh:icons.RefreshCw, connections:icons.Plug, audit:icons.ScrollText, policies:icons.ShieldCheck, usage:icons.Cpu, chevron:icons.ChevronDown, right:icons.ChevronRight, menu:icons.PanelLeft, bell:icons.Bell, close:icons.X, send:icons.ArrowUp, stop:icons.Square, plus:icons.SquarePen, users:icons.Users, logout:icons.LogOut, key:icons.KeyRound, link:icons.ExternalLink, check:icons.Check, clock:icons.Clock, file:icons.FileText, bank:icons.Landmark, invoicing:icons.Database, ksef:icons.FileCheck2, mail:icons.Mail, laptop:icons.Laptop, bot:icons.Bot, resolved:icons.CircleCheck, forecast:icons.LineChart, folder:icons.Folder, slack:icons.Send, alert:icons.AlertCircle })) {
    fs.writeFileSync(path.join(output, 'icons', name+'.svg'), renderToStaticMarkup(React.createElement(component, { width:16, height:16, strokeWidth:2, 'aria-hidden':'true' })));
  }
}
main().catch(error => { console.error(error.message); process.exitCode=1; });
