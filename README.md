# mo-chaos工具简介：
## 主要目录文件介绍：
     chaos-mesh/ : 存放chaos-mesh的helm chart目录
     thread/ : chaos线程、test线程、controller线程
     config/ : 存放配置chaos 和 test 的配置yaml文件，用户设计好对应的chaos故障配置文件保存在里面
     mo_chaos_test.py : 执行chaos 的主文件
     logs/ : 程序运行时自动创建，产生log文件的目录
     test-tool/ : 测试工具目录存放目录
     test-report/ : 测试工具产生的log 存放的目录

## 部署 chaos-mesh
     下载chaos-mesh的helm chart到执行机器上面
     创建ns kubectl create ns <namespace>
     之后在ns 创建下载镜像用户密码的 secret，如果没有权限联系吴叶磊开通权限 
     ps：机房机器无法下载海外镜像，目前将镜像转存到公司阿里云镜像repo里面
     kubectl create secret docker-registry aliyun --docker-server=registry.cn-hangzhou.aliyuncs.com/mocloud --docker-username= --docker-password= -n <namespace>
     helm部署： helm 部署命令 helm install chaos-mesh ./chaos-mesh -n=<namespace> --set chaosDaemon.runtime=containerd --set chaosDaemon.socketPath=/run/containerd/containerd.sock
   
## 使用命令
   python mo_chaos_test.py -c <chaos yaml文件名称>  -t  <test yaml文件名称>
    
## chaos yaml文件结构介绍
样例
```yaml
chaos:
  cm-chaos:
    - name: task_a1
      kubectl_yaml: |
        apiVersion: chaos-mesh.org/v1alpha1
        kind: PodChaos
        metadata:
          name: pod-kill-01
          namespace: mocluster2
        spec:
          action: pod-kill
          mode: one
          selector:
            namespaces:
              - mocluster2
            labelSelectors:
              'matrixorigin.io/component': 'CNSet'
      times: 2
      interval: 10
      is_delete_after_apply: true
    - name: task_a2
      kubectl_yaml: |
        kind: NetworkChaos
        apiVersion: chaos-mesh.org/v1alpha1
        metadata:
          namespace: mocluster2
          name: network-delay-pod
        spec:
          selector:
            namespaces:
              - mocluster2
            labelSelectors:
              'matrixorigin.io/component': 'CNSet'
          mode: all
          action: delay # 网络故障类型，延迟
          duration: 20m # 持续时间, 这个为了持续观察，所以配置的时间较长
          delay:
            latency: 100ms # 延迟时间
            correlation: '0.5' # 表示延迟时间的时间长度与前一次延迟时长的相关性
            jitter: 20ms # 表示延迟时间的变化范围
      times: 3
      interval: 10
      is_delete_after_apply: true
  sql-chaos:
    - name: sql-chaos01
      type: database_flush_chaos
      dbname: tpcc
      interval: 10
      times: 2
    - name: sql-chaos02
      type: table_flush_chaos
      dbname: tpcc
      tablename: bmsql_new_order
      interval: 10
      times: 2
    - name: sql-chaos03
      type: database_merge_chaos
      dbname: tpcc
      interval: 10
      times: 2
    - name: sql-chaos04
      type: table_merge_chaos
      dbname: tpcc
      tablename: bmsql_new_order
      interval: 10
      times: 2
    - name: sql-chaos05
      type: checkpoint_chaos
      interval: 10
      times: 2
  chaos_combination:
    mode: "in-turn"  # or "random-turn"
  mo-env:
      host: "10.222.4.14"
      user: "dump"
      port: 31429
      password: "111"
  namespace: "mocluster2"
```
    1、节点cm-chaos： 配置chaos-mesh类型故障
           name：代表节点名称
           kubectl_yaml： chaos-mesh故障 kubectl apply -f 对应的配置文件 ，
                          可以参考[chaos-mesh 官方文档](https://chaos-mesh.org/zh/docs/) pod-kill、network-delay、io-delay等类型故障
           times: 代表这个故障执行多少次
           interval：每次执行完sleep多少秒
           is_delete_after_apply： 执行完 之后sleep interval 之后 是否删除 默认填true 删除
    2、节点sql-chaos： 配置sql类型故障
           name： 代表节点名称
           type： 目前有5种类型 
           dbname： database <对于checkpoint_chaos 不需要>
           tablename： 表名称 <对于checkpoint_chaos 、database_flush_chaos、database_merge_chaos 不需要>
           times:代表这个故障执行多少次
           interval：每次执行完sleep 多少秒
    3、节点chaos_combination： 配置chaos 运行方式
           mode：in-turn （轮流执行cm-chaos 和 sql-chaos）
                 random-turn（随机选择cm-chaos 和 sql-chaos）
    4、节点mo-env: 配置连接mo的基本配置 （host、port、user、password）
    5、节点namespace: 代表服务部署对应的ns


## test yaml 文件配置介绍
```yaml
tasks:
- name: mo-tpcc
  work-path: mo-tpcc
  run-steps:
    - command: ./runBenchmark.sh props.mo
  verify:
    - command: ./runVerify.sh props.mo
  verify-mode: parallel
  log-paths:
    - path: benchmarksql-info.log
    - path: benchmarksql-error-10-2.log
- name: mo-tpcc-2
  work-path: mo-tpcc-2
  run-steps:
    - command: ./runBenchmark.sh props.mo
  verify:
    - command: ./runVerify.sh props.mo
  verify-mode: after
  log-paths:
    - path: benchmarksql-info.log
    - path: benchmarksql-error-10-2.log
```
       tasks: 下面挂载多个task节点，并行运行
         name： 代表task name
         work-path: 代表测试工具工作目录
         run-steps: 配置 test 任务的执行命令
         verify: 配置验证测试结果的执行命令
         verify-mode： parallel代表和测试任务一起运行循环执行，after代表执行完test任务命令之后再执行
         log-paths: 配置测试工具生成的log文件，跑完任务会cp 到 test-report/ 目录下

## yaml文件配置路径
   放入config 目录下，执行的时候 python mo_chaos_test.py -c <chaos yaml文件名称>  -t  <test yaml文件名称> 带文件名称不要带路径，程序只会到config找同名文件

## 使用TIPS
     A、在配置chaos-mesh单个故障时不要配置schedule类型故障，把周期性任务通过交给程序（times、interval）来做，
        配置单个chaos故障，只配置基础的不要配置组合场景，我们执行的过程都有log记录
     B、logs下有对应log文件产生可以通过观测log确认运行情况
     C、使用之前确认chaos-mesh 和mo 服务都部署在 k8s集群，执行程序的机器的kubectl 能访问对应的k8s集群
     D、使用之前通过pip install -r requirements.txt
     E、使用python版本需要3.11
