import assert from 'node:assert/strict'
import test from 'node:test'
import { buildProcessTree } from '../src/processTree.ts'

test('process trees preserve actual parent-child nesting and input order',()=>{
  const tree=buildProcessTree([
    {pid:10,parent_pid:1,name:'shell'},
    {pid:30,parent_pid:20,name:'agent'},
    {pid:20,parent_pid:10,name:'launcher'},
    {pid:40,parent_pid:10,name:'sibling'},
  ])

  assert.deepEqual(tree.map(node=>node.process.pid),[10])
  assert.deepEqual(tree[0].children.map(node=>node.process.pid),[20,40])
  assert.deepEqual(tree[0].children[0].children.map(node=>node.process.pid),[30])
})

test('missing parents and malformed cycles remain visible as roots',()=>{
  const tree=buildProcessTree([
    {pid:10,parent_pid:999},
    {pid:20,parent_pid:30},
    {pid:30,parent_pid:20},
  ])

  assert.deepEqual(tree.map(node=>node.process.pid),[10,20,30])
})
