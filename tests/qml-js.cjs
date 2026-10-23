const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
module.exports = function load(name) {
  let source = fs.readFileSync(path.join(__dirname, '..', name), 'utf8')
  const context = vm.createContext({})
  source = source.replace(/^\.pragma library\s*$/gm, '').replace(/^\.import "([^"]+)" as (\w+)\s*$/gm, (_, file, alias) => {
    context[alias] = load(file)
    return ''
  })
  vm.runInContext(source, context)
  return context
}
