const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path')
const source=fs.readFileSync(path.join(__dirname,'../Service.qml'),'utf8')
const Logic=require('../NotificationLogic.js')
function fixture(){
 const row={timestamp:1000,originalId:1,appIcon:'foot',image:'file:///tmp/new.png'}
 const state=vm.createContext({NotificationLogic:Logic,imagesDir:'/tmp/images/',normalizedImages:{},imageWriteVersions:{'1000-1':1},popupModel:{count:1,get(){return row}},isRestoredRow(){return false}})
 for(const name of ['acceptNormalizedImages','popupImageFor','forgetNormalizedImages']) {
  const start=source.indexOf('  function '+name+'(')
  vm.runInContext(source.slice(start,source.indexOf('\n  }',start)+4),state)
 }
 return {state,row}
}
test('only matching conversions publish; stale replacements and dismissed rows cannot gain images',()=>{
 const {state,row}=fixture()
 const normalized='file:///tmp/images/1000-1-image-'+'a'.repeat(64)+'.png'
 const result={key:'1000-1',version:1,sources:{appIcon:'foot',image:'file:///tmp/old.png'},appIcon:'foot',image:normalized}
 state.acceptNormalizedImages(result)
 assert.equal(Object.keys(state.normalizedImages).length,0)
 assert.equal(state.popupImageFor(row,'image'),'')
 result.sources.image=row.image;result.version=0;state.acceptNormalizedImages(result)
 assert.equal(Object.keys(state.normalizedImages).length,0)
 result.version=1;state.acceptNormalizedImages(result)
 assert.equal(state.popupImageFor(row,'image'),normalized)
 assert.equal(state.popupImageFor(row,'appIcon'),'foot')
 row.image='file:///tmp/replacement.png'
 assert.equal(state.popupImageFor(row,'image'),'')
 state.forgetNormalizedImages(row);state.popupModel.count=0;state.acceptNormalizedImages(result)
 assert.equal(Object.keys(state.normalizedImages).length,0)
})
test('restored originals and arbitrary provider URLs fail closed at the actual service boundary',()=>{
 const {state,row}=fixture()
 state.isRestoredRow=()=>true
 for(const value of ['file:///tmp/progressive.jpg','image://qsimage/123','file:///tmp/images/../outside.png']) {
  row.image=value;row.appIcon=value
  assert.equal(state.popupImageFor(row,'image'),'')
  assert.equal(state.popupImageFor(row,'appIcon'),'')
 }
 const normalized='file:///tmp/images/1000-1-image-'+'a'.repeat(64)+'.png'
 row.image=normalized;assert.equal(state.popupImageFor(row,'image'),normalized)
})
