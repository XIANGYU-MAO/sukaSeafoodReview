import {
  cpSync,
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(scriptDirectory, "..");
const repositoryRoot = resolve(webRoot, "..");

function assertOutputInside(publicRoot, outputDirectory) {
  const relativePath = relative(resolve(publicRoot), resolve(outputDirectory));
  if (relativePath.startsWith("..") || relativePath === "") {
    throw new Error(`refusing unsafe static output path: ${outputDirectory}`);
  }
}

function singleInlineBlock(source, tagName) {
  const expression = new RegExp(`<${tagName}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${tagName}>`, "gi");
  const matches = [...source.matchAll(expression)].filter((match) => {
    if (tagName !== "script") return true;
    return !/\ssrc\s*=/i.test(match[0]);
  });
  if (matches.length !== 1) {
    throw new Error(`validator must contain exactly one inline <${tagName}> block`);
  }
  return matches[0];
}

export function buildStaticAssets({
  validatorSource = join(repositoryRoot, "validator.html"),
  portalSource = join(webRoot, "static", "portal"),
  publicRoot = join(webRoot, "public"),
  skipPortal = false,
} = {}) {
  const validatorOutput = join(publicRoot, "validator");
  assertOutputInside(publicRoot, validatorOutput);

  const source = readFileSync(validatorSource, "utf8");
  const style = singleInlineBlock(source, "style");
  const script = singleInlineBlock(source, "script");
  const html = source
    .replace(style[0], '<link rel="stylesheet" href="./validator.css">')
    .replace(script[0], '<script src="./validator.js" defer></script>');

  rmSync(validatorOutput, { recursive: true, force: true });
  mkdirSync(validatorOutput, { recursive: true });
  writeFileSync(join(validatorOutput, "index.html"), html, "utf8");
  writeFileSync(join(validatorOutput, "validator.css"), `${style[1].trim()}\n`, "utf8");
  writeFileSync(join(validatorOutput, "validator.js"), `${script[1].trim()}\n`, "utf8");

  if (!skipPortal && existsSync(portalSource)) {
    const portalOutput = join(publicRoot, "portal");
    assertOutputInside(publicRoot, portalOutput);
    rmSync(portalOutput, { recursive: true, force: true });
    cpSync(portalSource, portalOutput, {
      recursive: true,
      filter: (sourcePath) => !sourcePath.endsWith(".test.mjs"),
    });
  }
}

function parseArguments(argumentsList) {
  const options = {};
  for (let index = 0; index < argumentsList.length; index += 1) {
    const argument = argumentsList[index];
    if (argument === "--skip-portal") {
      options.skipPortal = true;
      continue;
    }
    const key = {
      "--validator-source": "validatorSource",
      "--portal-source": "portalSource",
      "--public-root": "publicRoot",
    }[argument];
    if (!key || !argumentsList[index + 1]) {
      throw new Error(`unknown or incomplete argument: ${argument}`);
    }
    options[key] = resolve(argumentsList[index + 1]);
    index += 1;
  }
  return options;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    buildStaticAssets(parseArguments(process.argv.slice(2)));
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  }
}
