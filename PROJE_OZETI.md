# Mininet ve Flower ile Dağıtık Federe Öğrenme Projesi

Bu proje, **Mininet** ağ emülatörü üzerinde **Flower** kütüphanesini kullanarak gerçekçi bir Federe Öğrenme (Federated Learning) ortamı simüle etmeyi amaçlar.

## 📂 Gereksiz Dosyalar
Şu anki proje yapısında aşağıdaki dosyalar artık gerekli değildir ve silinebilir:
- `download_cifar10.py`: Yerini `download_dataset.py` aldı.
- `run_fl_training.sh`: Tüm akış `start_mininet_fl.sh` içine entegre edildi.

---

## 🏗️ Proje Mimarisi

Proje, tek bir fiziksel makine üzerinde sanal bir ağ topolojisi oluşturur ve bu ağ üzerindeki düğümlerde (nodes) Federe Öğrenme süreçlerini çalıştırır.

### 1. Ağ Topolojisi (Mininet)
- **Yapı:** 1 Sunucu (Server), 1 Switch (Open vSwitch), 4 İstemci (Client).
- **İletişim:** Tüm düğümler sanal bir switch üzerinden birbirine bağlıdır.
- **Dosya:** `mininet_topology.py`

### 2. Federe Öğrenme Yapısı (Flower)
Flower'ın "Next-Gen" mimarisi (SuperLink ve SuperNode) kullanılmaktadır.

- **Sunucu (Server Node):**
  - **SuperLink:** İstemcilerle iletişimi yöneten ve global modelin durumunu tutan ana bileşen.
  - **ServerApp:** Federe öğrenme stratejisini (örn. FedAvg) çalıştıran uygulama.
  - **Görevi:** İstemcilerden gelen model güncellemelerini toplar (Aggregation), ortalamasını alır ve yeni global modeli dağıtır.

- **İstemciler (Client Nodes - h1, h2, h3, h4):**
  - **SuperNode:** Flower'ın istemci tarafındaki ajanı.
  - **ClientApp:** Yerel veri üzerinde eğitimi gerçekleştiren uygulama (`task.py`).
  - **Görevi:** Sunucudan gelen global modeli alır, kendi yerel verisiyle eğitir ve güncellenmiş ağırlıkları sunucuya geri gönderir.

## 🚀 Çalışma Mantığı ve Akış

1.  **Başlatma (`start_mininet_fl.sh`):**
    - Önce `download_dataset.py` çalıştırılarak CIFAR-10 verisetinin indirildiğinden emin olunur.
    - Mininet başlatılır ve sanal ağ kurulur.
    - Sunucu düğümünde `flower-superlink` başlatılır.
    - İstemci düğümlerinde `flower-supernode` başlatılır.

2.  **Veri Yükleme (`task.py` & `download_dataset.py`):**
    - **Sorun:** Her istemcinin aynı anda veri indirmeye çalışması hata ve performans kaybı yaratıyordu.
    - **Çözüm:** Veri seti önceden `download_dataset.py` ile indirilir (`torchvision` formatında).
    - `task.py`, `CIFAR10_DATASET_ROOT` ortam değişkenini kullanarak bu indirilmiş veriyi okur.
    - Veri seti 4 parçaya bölünür (partitioning) ve her istemci sadece kendi payına düşen veriyi yükler.

3.  **Eğitim (Training):**
    - **Model:** Basit bir CNN (Convolutional Neural Network).
    - Her istemci, kendi yerel verisi üzerinde 1 epoch eğitim yapar.
    - Eğitilen modelin ağırlıkları sunucuya gönderilir.

4.  **Doğrulama (Verification):**
    - Sunucu logları (`server.log`), eğitim kaybının (loss) düştüğünü ve doğrulama başarımının (accuracy) arttığını gösterir.
    - Bu, sistemin doğru çalıştığını ve düğümler arası veri/model akışının başarılı olduğunu kanıtlar.

## 🛠️ Temel Dosyalar
- **`start_mininet_fl.sh`:** Projeyi başlatan ana script.
- **`mininet_topology.py`:** Mininet ağını ve Flower bileşenlerini başlatan Python kodu.
- **`flower-distributed/flower_distributed/task.py`:** Model mimarisi ve eğitim fonksiyonlarını içeren kod.
- **`download_dataset.py`:** Veri setini indiren yardımcı script.

## 💻 Kullanılması Gereken Komutlar

Projeyi çalıştırmak için aşağıdaki adımları takip edin:

### 1. Simülasyonu Başlatma
Terminalde proje dizinine gidin ve başlatma scriptini çalıştırın:
```bash
cd /home/alizekaid/Desktop/Flower_distributed
sudo bash start_mininet_fl.sh
```
*Bu komut veri setini kontrol eder, Mininet ağını kurar ve Flower servislerini başlatır.*

### 2. Eğitimi Başlatma (Mininet CLI)
Mininet komut satırı (`mininet>`) açıldığında, eğitimi başlatmak için şu komutu girin:
```bash
server flwr run /home/alizekaid/Desktop/Flower_distributed/flower-distributed --run-config num-server-rounds=3
```
*Bu komut sunucu üzerinde 3 turluk federe öğrenme sürecini başlatır.*

### 3. İzleme ve Kontrol
Mininet CLI üzerinde ağ bağlantısını test etmek için:
```bash
pingall
```

### 4. Çıkış
Simülasyonu durdurmak ve çıkmak için:
```bash
exit
```

